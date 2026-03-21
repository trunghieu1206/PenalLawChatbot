"""
Vietnamese Legal AI Chatbot — FastAPI AI Service
Enhanced LangGraph pipeline with:
  - Fact extraction node
  - Law mapping node
  - Deterministic sentencing calculation
  - Rebuttal mode
  - Structured legal argument generation
"""

import os
# ⚠️ MUST be before any pymilvus/langchain_milvus imports:
# pymilvus reads MILVUS_URI from os.environ at import time (Connections singleton).
# If it contains a file path instead of http://..., it crashes immediately.
os.environ.pop("MILVUS_URI", None)

import re
import json
import numpy as np
import torch
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Literal, Annotated, Sequence, TypedDict, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from huggingface_hub import login
from transformers import AutoModel, AutoTokenizer
from peft import PeftModel

# LangChain & AI Imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus import Milvus
from langgraph.graph import END, StateGraph, START
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

# Load Environment Variables
load_dotenv()

# --- CONFIGURATION ---
MILVUS_URI = os.getenv("MILVUS_URI", "./VN_law_lora.db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "legal_rag_lora")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TOP_K = int(os.getenv("TOP_K", "15"))

# --- GLOBAL STATE ---
app_state: Dict[str, Any] = {}


# ===========================================================
# CUSTOM EMBEDDING CLASS — uses PEFT to load LoRA adapter
# ===========================================================
class LoRABGEM3Embeddings(Embeddings):
    def __init__(self, base_model_name: str, adapter_name: str, device: str = "cuda"):
        print(f"🔄 Loading BGE-M3 base model on {device}...")
        self.device = device

        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            base_model_name, trust_remote_code=True
        )

        # Load base transformer model
        base_model = AutoModel.from_pretrained(
            base_model_name, trust_remote_code=True
        )

        # Apply LoRA via PEFT (handles base_model.model.* keys correctly)
        print(f"⬇️  Applying LoRA adapter via PEFT: {adapter_name}")
        try:
            peft_model = PeftModel.from_pretrained(base_model, adapter_name)
            # Merge weights into base model for faster inference
            self.model = peft_model.merge_and_unload()
            print("✅ LoRA adapter merged successfully — no key mismatches!")
        except Exception as e:
            print(f"⚠️  Could not load LoRA adapter: {e}. Falling back to base model.")
            self.model = base_model

        self.model = self.model.to(device)
        self.model.eval()

    def _mean_pooling(self, model_output, attention_mask):
        """Mean pool token embeddings, weighted by attention mask."""
        token_embeddings = model_output[0]  # (batch, seq, hidden)
        mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * mask_expanded, 1) / torch.clamp(
            mask_expanded.sum(1), min=1e-9
        )

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        with torch.no_grad():
            output = self.model(**encoded)
        embeddings = self._mean_pooling(output, encoded["attention_mask"])
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().numpy()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        all_embeddings: List[List[float]] = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            all_embeddings.extend(self._encode_batch(batch).tolist())
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        return self._encode_batch([text])[0].tolist()


# ===========================================================
# LANGGRAPH STATE
# ===========================================================
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    question: str
    full_case_content: str
    documents: List[Document]
    retry_count: int
    user_role: Literal["defense", "victim", "neutral"]
    extracted_facts: Optional[Dict[str, Any]]
    mapped_laws: Optional[List[Dict[str, str]]]
    rebuttal_against: Optional[str]
    sentencing_data: Optional[Dict[str, Any]]


# ===========================================================
# REQUEST / RESPONSE MODELS
# ===========================================================
class RequestBody(BaseModel):
    case_content: str
    role: Literal["defense", "victim", "neutral"] = "neutral"
    rebuttal_against: Optional[str] = None


class PredictResponse(BaseModel):
    result: str
    extracted_facts: Optional[Dict[str, Any]] = None
    mapped_laws: Optional[List[Dict[str, str]]] = None
    sentencing_data: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    device: str
    model_loaded: bool


class GradeDocuments(BaseModel):
    binary_score: str = Field(description="Relevance score 'yes' or 'no'")


# ===========================================================
# UTILITY: DETERMINISTIC SENTENCING CALCULATIONS
# ===========================================================
def parse_date(text: str, patterns: List[str]) -> Optional[datetime]:
    """Try multiple date formats to parse a date string."""
    for pattern in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    return None


def compute_detention_months(arrest_date_str: str, trial_date_str: str) -> Optional[float]:
    """Calculate months from arrest to trial."""
    d1 = parse_date(arrest_date_str, [])
    d2 = parse_date(trial_date_str, [])
    if d1 and d2 and d2 > d1:
        delta = d2 - d1
        return round(delta.days / 30.44, 1)
    return None


def compute_age_at_crime(dob_str: str, crime_date_str: str) -> Optional[float]:
    """Calculate victim/defendant age at time of crime."""
    dob = parse_date(dob_str, [])
    crime_date = parse_date(crime_date_str, [])
    if dob and crime_date:
        age = (crime_date - dob).days / 365.25
        return round(age, 2)
    return None


def extract_sentencing_data(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically compute numeric sentencing data from extracted facts.
    Returns structured data to augment the LLM prompt.
    """
    result: Dict[str, Any] = {}

    # Detention period
    arrest_date = facts.get("ngay_tam_giam")
    trial_date = facts.get("ngay_xet_xu")
    if arrest_date and trial_date:
        months = compute_detention_months(arrest_date, trial_date)
        result["detention_months"] = months

    # Victim age at crime
    victim_dob = facts.get("ngay_sinh_nan_nhan")
    crime_date = facts.get("ngay_pham_toi")
    if victim_dob and crime_date:
        age = compute_age_at_crime(victim_dob, crime_date)
        result["victim_age_at_crime"] = age
        result["victim_is_minor"] = age is not None and age < 18

    # Defendant age at crime
    defendant_dob = facts.get("ngay_sinh_bi_cao")
    if defendant_dob and crime_date:
        age = compute_age_at_crime(defendant_dob, crime_date)
        result["defendant_age_at_crime"] = age
        result["defendant_is_minor"] = age is not None and age < 18

    return result


# ===========================================================
# LIFESPAN — LOAD MODELS ONCE
# ===========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 SERVER STARTUP: Initializing...")

    # Authenticate HuggingFace
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        try:
            login(token=hf_token)
            print("✅ Logged in to Hugging Face successfully.")
        except Exception as e:
            print(f"⚠️  Failed HF login: {e}")
    else:
        print("⚠️  'HF_TOKEN' not set. Public models only.")

    # 1. Embedding model
    embedding_model = LoRABGEM3Embeddings(
        base_model_name="BAAI/bge-m3",
        adapter_name=os.getenv("EMBEDDING_ADAPTER", "trunghieu1206/lawchatbot-40k"),
        device=DEVICE
    )

    # 2. Milvus-Lite vector store
    # Key: use uri= (Milvus Lite file path) and text_field="content"
    # (the embed script stores article text in the "content" field, not "text")
    print(f"📦 Connecting to Milvus Lite DB: {MILVUS_URI}")
    vectorstore = Milvus(
        embedding_function=embedding_model,
        connection_args={"uri": MILVUS_URI},
        collection_name=COLLECTION_NAME,
        text_field="content",
        drop_old=False,
        auto_id=True,
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": TOP_K})
    print(f"✅ Milvus ready — collection '{COLLECTION_NAME}'")

    # 3. LLM (OpenRouter)
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "google/gemini-2.5-flash"),
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0
    )

    # -------------------------------------------------------
    # NODE DEFINITIONS
    # -------------------------------------------------------

    # NODE: EXTRACT FACTS
    def extract_facts_node(state: AgentState) -> dict:
        """Extract structured legal facts from case text."""
        print("---EXTRACTING FACTS---")
        case_text = state.get("full_case_content", state["question"])

        system_prompt = """Bạn là một chuyên gia phân tích hồ sơ pháp lý.
Nhiệm vụ: Đọc kỹ nội dung vụ án và trích xuất thông tin có cấu trúc.
Trả về JSON với các trường sau (dùng null nếu không tìm thấy thông tin):
{
  "hanh_vi": "mô tả hành vi phạm tội",
  "hau_qua": "hậu quả gây ra",
  "dong_co": "động cơ",
  "doi_tuong": "đối tượng bị hại",
  "cong_cu": "công cụ phương tiện",
  "tinh_tiet_tang_nang": ["list tình tiết tăng nặng có trong vụ án"],
  "tinh_tiet_giam_nhe": ["list tình tiết giảm nhẹ có trong vụ án"],
  "ngay_pham_toi": "dd/mm/yyyy",
  "ngay_sinh_nan_nhan": "dd/mm/yyyy",
  "ngay_sinh_bi_cao": "dd/mm/yyyy",
  "ngay_tam_giam": "dd/mm/yyyy",
  "ngay_xet_xu": "dd/mm/yyyy",
  "ten_bi_cao": "tên bị cáo",
  "co_tien_an": true/false,
  "da_boi_thuong": true/false,
  "da_thanh_khan_khai_bao": true/false
}
OUTPUT: CHỈ xuất JSON hợp lệ, không markdown, không giải thích."""

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"NỘI DUNG VỤ ÁN:\n{case_text}")
            ])
            raw = response.content.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"```json?\s*", "", raw).strip("`").strip()
            facts = json.loads(raw)
        except Exception as e:
            print(f"⚠️  Fact extraction failed: {e}")
            facts = {}

        # Deterministic sentencing data
        sentencing_data = extract_sentencing_data(facts)
        print(f"  Facts: {list(facts.keys())}")
        print(f"  Sentencing data: {sentencing_data}")

        return {"extracted_facts": facts, "sentencing_data": sentencing_data}

    # NODE: REWRITE
    def rewrite_question(state: AgentState) -> dict:
        print("---REWRITING QUERY---")
        role = state.get("user_role", "neutral")
        question = state["question"]

        bias_keywords = ""
        if role == "defense":
            bias_keywords = "khung hình phạt thấp nhất, tình tiết giảm nhẹ, án treo"
        elif role == "victim":
            bias_keywords = "khung hình phạt cao nhất, tình tiết tăng nặng, bồi thường dân sự"

        system_msg = (
            "Bạn là một chuyên gia Tìm kiếm Pháp lý.\n"
            "Nhiệm vụ: Viết lại câu hỏi thành truy vấn tìm kiếm tối ưu cho CSDL luật.\n\n"
            "QUY TẮC:\n"
            "1. GIỮ NGUYÊN MỐC THỜI GIAN (năm, ngày tháng) trong truy vấn.\n"
            "2. LOẠI BỎ tên riêng, địa danh không cần thiết.\n"
            "3. CHUẨN HÓA sang thuật ngữ pháp lý.\n"
            f"4. THÊM từ khóa: {bias_keywords}\n\n"
            "OUTPUT: CHỈ xuất ra câu truy vấn (String). Không giải thích."
        )
        response = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=f"NỘI DUNG:\n{question}")
        ])
        cleaned = response.content.strip().replace('"', '').replace("'", "")
        print(f"  Rewritten: {cleaned[:100]}")
        return {"question": cleaned, "retry_count": state.get("retry_count", 0) + 1}

    # NODE: RETRIEVE
    def retrieve_node(state: AgentState) -> dict:
        print("---RETRIEVING---")
        docs = retriever.invoke(state["question"])
        return {"documents": docs}

    # NODE: GRADE DOCUMENTS
    def grade_documents(state: AgentState) -> dict:
        print("---GRADING DOCUMENTS---")
        question = state["question"]
        documents = state["documents"]
        structured_llm = llm.with_structured_output(GradeDocuments)
        chain = (
            ChatPromptTemplate.from_template(
                "Câu hỏi: {question}\nTài liệu: {document}\nTài liệu có liên quan không? Trả lời 'yes' hoặc 'no'."
            )
            | structured_llm
        )
        filtered = []
        for d in documents:
            try:
                res = chain.invoke({"question": question, "document": d.page_content})
                if res.binary_score.lower() == "yes":
                    filtered.append(d)
            except Exception:
                continue

        if not filtered:
            print("⚠️  All filtered — reverting to original list")
            return {"documents": documents, "is_relevant": True}

        return {"documents": filtered, "is_relevant": bool(filtered)}

    def check_relevance(state: AgentState) -> str:
        if state.get("is_relevant") or state.get("retry_count", 0) >= 1:
            return "generate"
        return "rewrite"

    def check_rebuttal(state: AgentState) -> str:
        if state.get("rebuttal_against"):
            return "rebuttal"
        return "extract_facts"

    # NODE: MAP LAWS
    def map_laws_node(state: AgentState) -> dict:
        """Map extracted facts to specific law articles."""
        print("---MAPPING LAWS---")
        facts = state.get("extracted_facts", {})
        documents = state["documents"]
        case_text = state.get("full_case_content", state["question"])

        context = "\n".join([d.page_content[:500] for d in documents[:5]])
        facts_str = json.dumps(facts, ensure_ascii=False, indent=2)

        system_prompt = """Bạn là chuyên gia luật hình sự Việt Nam.
Dựa vào các sự kiện được trích xuất và văn bản luật, hãy ánh xạ từng hành vi phạm tội vào điều khoản cụ thể.
Trả về JSON array:
[
  {
    "article": "Điều 255",
    "clause": "Khoản 2",
    "offense_name": "Tội tổ chức sử dụng trái phép chất ma túy",
    "applicable_reason": "Lý do áp dụng điều này"
  }
]
OUTPUT: CHỈ JSON array hợp lệ."""

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"SỰ KIỆN:\n{facts_str}\n\nVĂN BẢN LUẬT:\n{context}\n\nVỤ ÁN:\n{case_text[:1000]}")
            ])
            raw = re.sub(r"```json?\s*", "", response.content.strip()).strip("`").strip()
            mapped = json.loads(raw)
        except Exception as e:
            print(f"⚠️  Law mapping failed: {e}")
            mapped = []

        return {"mapped_laws": mapped}

    # NODE: GENERATE
    def generate(state: AgentState) -> dict:
        print("---GENERATING JUDGMENT---")
        case_details = state.get("full_case_content", state["question"])
        documents = state["documents"]
        role = state.get("user_role", "neutral")
        sentencing_data = state.get("sentencing_data", {})
        mapped_laws = state.get("mapped_laws", [])

        if not documents:
            return {"messages": [AIMessage(content="Xin lỗi, tôi chưa tìm thấy văn bản luật phù hợp.")]}

        context_text = "\n\n".join([
            f"[Nguồn: {d.metadata.get('source', 'Unknown')}]\n{d.page_content}"
            for d in documents
        ])

        # Role-specific instructions
        if role == "defense":
            role_instruction = "VAI TRÒ: LUẬT SƯ BÀO CHỮA cho bị cáo. Mục tiêu: Tìm mọi căn cứ để giảm nhẹ hình phạt xuống mức thấp nhất (hoặc Án treo). Đứng trên góc nhìn luật sư bào chữa, không phải tòa án."
            advice_section_instruction = "\n**III. KHUYẾN NGHỊ CHO THÂN CHỦ:**\n(Đưa ra các bước cụ thể: bồi thường, xin giấy bãi nại, nộp án phí...)"
        elif role == "victim":
            role_instruction = "VAI TRÒ: LUẬT SƯ BẢO VỆ BỊ HẠI. Mục tiêu: Yêu cầu xử nghiêm minh và bồi thường tối đa."
            advice_section_instruction = "\n**III. KHUYẾN NGHỊ CHO GIA ĐÌNH BỊ HẠI:**\n(Hướng dẫn thu thập hóa đơn, chứng từ thiệt hại, yêu cầu cấp dưỡng...)"
        else:
            role_instruction = "VAI TRÒ: THẨM PHÁN CHỦ TỌA. Tư duy: Lạnh lùng, Chính xác, Chỉ dựa trên chứng cứ trong hồ sơ."
            advice_section_instruction = ""

        # Deterministic data context
        det_context = ""
        if sentencing_data:
            lines = []
            if "detention_months" in sentencing_data:
                lines.append(f"- Thời gian tạm giam đã tính: {sentencing_data['detention_months']} tháng")
            if "victim_age_at_crime" in sentencing_data:
                minor = sentencing_data.get("victim_is_minor", False)
                lines.append(f"- Tuổi nạn nhân tại thời điểm phạm tội: {sentencing_data['victim_age_at_crime']} tuổi ({'DƯỚI 18 TUỔI' if minor else 'TRÊN 18 TUỔI'})")
            if "defendant_age_at_crime" in sentencing_data:
                lines.append(f"- Tuổi bị cáo tại thời điểm phạm tội: {sentencing_data['defendant_age_at_crime']} tuổi")
            if lines:
                det_context = "\n\n⚙️  DỮ LIỆU ĐÃ TÍNH TOÁN CHÍNH XÁC (BẮT BUỘC SỬ DỤNG):\n" + "\n".join(lines)

        mapped_context = ""
        if mapped_laws:
            mapped_context = "\n\n📋 ÁNH XẠ ĐIỀU LUẬT ĐỀ XUẤT:\n" + "\n".join([
                f"- {m.get('offense_name', '')} → {m.get('article', '')} {m.get('clause', '')}: {m.get('applicable_reason', '')}"
                for m in mapped_laws
            ])

        prompt_template = """{role_instruction}

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy ra PHÁN QUYẾT CỤ THỂ.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
----------------

MỘT VÀI LƯU Ý:
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 BLHS mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 BLHS mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không (ưu tiên phạt tiền với Điều 201).
5. Phạm tội chưa đạt (Điều 15, 57): áp dụng quy tắc 3/4.

QUY TRÌNH TƯ DUY LƯỢNG HÌNH (BẮT BUỘC THEO THỨ TỰ):
- KHÔNG GIẢ ĐỊNH: chỉ dùng tình tiết có trong case_details.
- NGUYÊN TẮC CÓ LỢI (Thời gian): tội trước 2018 → áp dụng Luật 2015/2017 nếu nhẹ hơn.
- NGUYÊN TẮC ĐỘC LẬP XÉT XỬ: đề nghị VKS chỉ là tham khảo.

BƯỚC 1: KIỂM TRA ÁN BẰNG THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 2: KIỂM TRA ĐỘ TUỔI (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 3: ĐỊNH TỘI DANH.
BƯỚC 4: LƯỢNG HÌNH CHO TỪNG TỘI.
BƯỚC 5: TỔNG HỢP HÌNH PHẠT (Điều 55).
BƯỚC 5.5: TỔNG HỢP VỚI BẢN ÁN CŨ (nếu có).
BƯỚC 6: QUYẾT ĐỊNH HÌNH THỨC CHẤP HÀNH (Án treo chỉ khi tổng án ≤ 3 năm).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. NHẬN ĐỊNH CỦA TÒA ÁN:**
1. **Định tội danh:** (liệt kê từng hành vi + điều khoản + khung hình phạt)
2. **Phân tích tình tiết:**
   - Tình tiết Tăng nặng (Điều 52):
   - Tình tiết Giảm nhẹ (Điều 51):
3. **Nhân thân:**

**II. QUYẾT ĐỊNH:**
1. Tuyên bố bị cáo phạm tội...
2. Áp dụng điều khoản...
3. HÌNH PHẠT: (tù giam HOẶC phạt tiền, chọn 1)
4. TRÁCH NHIỆM DÂN SỰ & XỬ LÝ VẬT CHỨNG
5. ÁN PHÍ: 200.000 đồng

{advice_section_instruction}
"""

        prompt = ChatPromptTemplate.from_template(prompt_template)
        chain = prompt | llm | StrOutputParser()

        try:
            response = chain.invoke({
                "role_instruction": role_instruction,
                "advice_section_instruction": advice_section_instruction,
                "context": context_text,
                "case_details": case_details,
                "deterministic_context": det_context,
                "mapped_context": mapped_context,
            })
        except Exception as e:
            return {"messages": [AIMessage(content=f"Lỗi xử lý: {e}")]}

        return {"messages": [AIMessage(content=response)]}

    # NODE: REBUTTAL
    def rebuttal_node(state: AgentState) -> dict:
        """Generate legally sound counter-argument against provided argument."""
        print("---GENERATING REBUTTAL---")
        role = state.get("user_role", "neutral")
        opposing_arg = state.get("rebuttal_against", "")
        documents = state["documents"]
        case_details = state.get("full_case_content", state["question"])

        context_text = "\n\n".join([
            f"[Nguồn: {d.metadata.get('source', 'Unknown')}]\n{d.page_content}"
            for d in documents[:8]
        ])

        if role == "defense":
            rebuttal_role = "LUẬT SƯ BẢO VỆ BỊ HẠI phản bác luận điểm của luật sư bào chữa"
        elif role == "victim":
            rebuttal_role = "LUẬT SƯ BÀO CHỮA phản bác luận điểm của luật sư bị hại"
        else:
            rebuttal_role = "VIỆN KIỂM SÁT phân tích điểm yếu của luận điểm được đưa ra"

        prompt = f"""VAI TRÒ: {rebuttal_role}

LUẬN ĐIỂM CẦN PHẢN BÁC:
{opposing_arg}

VỤ ÁN:
{case_details[:2000]}

VĂN BẢN LUẬT THAM CHIẾU:
{context_text[:3000]}

Hãy:
1. Xác định điểm yếu pháp lý trong luận điểm trên.
2. Trích dẫn điều khoản luật phản bác.
3. Đưa ra luận điểm đối lập có căn cứ.

Cấu trúc:
**I. ĐIỂM YẾU TRONG LUẬN ĐIỂM ĐỐI PHƯƠNG:**
**II. CĂN CỨ PHẢN BÁC:**
**III. LUẬN ĐIỂM CỦA CHÚNG TÔI:**
"""
        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            return {"messages": [AIMessage(content=response.content)]}
        except Exception as e:
            return {"messages": [AIMessage(content=f"Lỗi: {e}")]}

    # -------------------------------------------------------
    # BUILD LANGGRAPH
    # -------------------------------------------------------
    workflow = StateGraph(AgentState)

    workflow.add_node("extract_facts", extract_facts_node)
    workflow.add_node("rewrite", rewrite_question)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("grade_documents", grade_documents)
    workflow.add_node("map_laws", map_laws_node)
    workflow.add_node("generate", generate)
    workflow.add_node("rebuttal", rebuttal_node)

    # Edges
    workflow.add_edge(START, "rewrite")
    workflow.add_edge("rewrite", "retrieve")
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        check_relevance,
        {"generate": "extract_facts", "rewrite": "rewrite"}
    )
    workflow.add_conditional_edges(
        "extract_facts",
        check_rebuttal,
        {"rebuttal": "rebuttal", "extract_facts": "map_laws"}
    )
    workflow.add_edge("map_laws", "generate")
    workflow.add_edge("generate", END)
    workflow.add_edge("rebuttal", END)

    app_compiled = workflow.compile()
    app_state["graph"] = app_compiled
    app_state["model_loaded"] = True

    print(f"✅ System Ready! Device: {DEVICE}")
    yield
    print("🛑 Shutting down...")


# ===========================================================
# FASTAPI APP
# ===========================================================
app = FastAPI(
    title="Vietnamese Legal AI Chatbot — AI Service",
    description="RAG-powered legal analysis using LangGraph + Milvus + OpenRouter",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="ok",
        device=DEVICE,
        model_loaded=app_state.get("model_loaded", False)
    )


@app.post("/predict", response_model=PredictResponse)
async def predict_judgment(req: RequestBody):
    graph = app_state.get("graph")
    if not graph:
        raise HTTPException(status_code=500, detail="Model not loaded")

    inputs = {
        "question": req.case_content,
        "full_case_content": req.case_content,
        "messages": [HumanMessage(content=req.case_content)],
        "user_role": req.role,
        "retry_count": 0,
        "documents": [],
        "extracted_facts": None,
        "mapped_laws": None,
        "sentencing_data": None,
        "rebuttal_against": req.rebuttal_against,
    }

    try:
        output = await graph.ainvoke(inputs)
        final_answer = output["messages"][-1].content
        return PredictResponse(
            result=final_answer,
            extracted_facts=output.get("extracted_facts"),
            mapped_laws=output.get("mapped_laws"),
            sentencing_data=output.get("sentencing_data"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
