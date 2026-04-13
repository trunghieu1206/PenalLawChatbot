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
# We NEVER set MILVUS_URI in the environment — instead we use MILVUS_DB_PATH
# so pymilvus never sees a file path and crashes with "Illegal uri".
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
from pymilvus import MilvusClient
from langgraph.graph import END, StateGraph, START
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

# Load Environment Variables
load_dotenv()

# --- CONFIGURATION ---
# Use MILVUS_DB_PATH (not MILVUS_URI) to avoid pymilvus reading it at import time.
# pymilvus specifically watches the MILVUS_URI env var — using a different name
# prevents the "Illegal uri: [/path/to/file]" ConnectionConfigException.
MILVUS_URI = os.getenv("MILVUS_DB_PATH", "./VN_law_lora.db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "legal_rag_lora")
TOP_K = int(os.getenv("TOP_K", "15"))


def _detect_device() -> str:
    """Detect and validate that CUDA is usable (GPU required).

    Runs a real tensor op to verify GPU kernels are compatible with this
    hardware (e.g. P104-100 / sm_61) — torch.cuda.is_available() is NOT
    sufficient; it only checks driver presence, not kernel compatibility.

    RAISES RuntimeError if GPU is not available or incompatible.
    """
    if os.getenv("FORCE_CPU", "0") == "1":
        print("⚙️  FORCE_CPU=1 — WARNING: Using CPU (intentional override for testing only)")
        return "cpu"

    if not torch.cuda.is_available():
        raise RuntimeError(
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "[GPU REQUIRED] CUDA is not available on this machine.\n"
            "This AI service requires GPU acceleration.\n"
            "  - Verify NVIDIA driver installed: nvidia-smi\n"
            "  - Verify PyTorch CUDA wheel matches driver version:\n"
            "    • Driver 12.0+  → install torch from cu121 wheel\n"
            "    • Driver 11.8   → install torch from cu118 wheel\n"
            "  - Run deploy_nodocker.sh to auto-detect and install correct wheel\n"
            "  - Contact: Check /var/log/penallaw/ai-service.log for details\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    try:
        probe = torch.zeros(1, device="cuda")
        _ = probe + 1  # triggers actual kernel dispatch
        del probe
        gpu_name = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        print(f"⚙️  GPU Ready — {gpu_name} (sm_{cap[0]}{cap[1]})")
        return "cuda"
    except Exception as e:
        raise RuntimeError(
            f"\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"[GPU PROBE FAILED] CUDA kernel test failed: {type(e).__name__}: {e}\n"
            f"  This usually means:\n"
            f"  - PyTorch CUDA version doesn't match driver version\n"
            f"  - GPU architecture not supported by this PyTorch build\n"
            f"  - GPU driver too old or incompatible\n"
            f"\n"
            f"  FIX: Re-run deploy_nodocker.sh\n"
            f"       It will detect your actual CUDA driver version and install\n"
            f"       the matching PyTorch wheel (cu118, cu121, or cu124)\n"
            f"\n"
            f"  Detected GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'unknown'}\n"
            f"  Check driver: nvidia-smi\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ) from e


DEVICE = _detect_device()

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
    chat_history: Optional[List[Dict[str, str]]]
    is_relevant: Optional[bool]   # set by grade_documents; must be declared or LangGraph drops it


# ===========================================================
# REQUEST / RESPONSE MODELS
# ===========================================================
class RequestBody(BaseModel):
    case_content: str
    role: Literal["defense", "victim", "neutral"] = "neutral"
    rebuttal_against: Optional[str] = None
    conversation_history: Optional[List[Dict[str, str]]] = Field(default_factory=list)


class PredictResponse(BaseModel):
    result: str
    extracted_facts: Optional[Dict[str, Any]] = None
    mapped_laws: Optional[List[Dict[str, Optional[str]]]] = None
    sentencing_data: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    device: str
    model_loaded: bool


class GradeDocuments(BaseModel):
    binary_score: str = Field(description="Relevance score 'yes' or 'no'")


class PracticeEvalRequest(BaseModel):
    case_description: str
    user_mode: Literal["defense", "victim", "neutral"] = "neutral"
    user_analysis: str


class PracticeEvalFeedback(BaseModel):
    strengths: List[str]
    improvements: List[str]
    missed_articles: List[str]
    suggestion: str


class PracticeEvalResponse(BaseModel):
    score: int
    feedback: PracticeEvalFeedback


# ===========================================================
# UTILITY: DETERMINISTIC SENTENCING CALCULATIONS
# ===========================================================
def parse_date(text: str) -> Optional[datetime]:
    """Try multiple date formats to parse a date string."""
    # BUG-05 FIX: Removed the unused `patterns` parameter — it was always ignored.
    for pattern in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    return None


def compute_detention_months(arrest_date_str: str, trial_date_str: str) -> Optional[float]:
    """Calculate months from arrest to trial."""
    d1 = parse_date(arrest_date_str)
    d2 = parse_date(trial_date_str)
    if d1 and d2 and d2 > d1:
        delta = d2 - d1
        return round(delta.days / 30.44, 1)
    return None


def compute_age_at_crime(dob_str: str, crime_date_str: str) -> Optional[float]:
    """Calculate victim/defendant age at time of crime."""
    dob = parse_date(dob_str)
    crime_date = parse_date(crime_date_str)
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

    # 2. Milvus-Lite vector store — use MilvusClient directly
    # (avoids langchain_milvus version issues and MILVUS_URI env collision)
    print(f"📦 Connecting to Milvus Lite DB: {MILVUS_URI}")
    milvus_client = MilvusClient(uri=MILVUS_URI)

    _OUTPUT_FIELDS = [
        "content", "article_number", "title",
        "chapter", "source", "effective_start", "effective_end",
    ]

    class _MilvusRetriever:
        """Thin retriever wrapping MilvusClient for similarity search."""
        def __init__(self, client, emb_fn, collection, top_k, output_fields):
            self._client = client
            self._emb = emb_fn
            self._col = collection
            self._k = top_k
            self._fields = output_fields

        def invoke(self, query: str):
            vec = self._emb.embed_query(query)
            results = self._client.search(
                collection_name=self._col,
                data=[vec],
                limit=self._k,
                output_fields=self._fields,
                search_params={"metric_type": "COSINE"},
            )[0]
            # --- LOG RAG CHUNK IDs ---
            print(f"  [RAG] Retrieved {len(results)} chunks:")
            for r in results:
                ch  = r['entity'].get('chapter', '?')
                art = r['entity'].get('article_number', '?')
                print(f"    ID={r['id']}  score={r['distance']:.4f}  | Chương: {ch}  Điều: {art}")
            # -------------------------
            docs = []
            for r in results:
                entity = r["entity"]
                docs.append(Document(
                    page_content=entity.get("content", ""),
                    metadata={
                        k: entity.get(k, "")
                        for k in self._fields if k != "content"
                    },
                ))
            return docs

    retriever = _MilvusRetriever(
        milvus_client, embedding_model, COLLECTION_NAME, TOP_K, _OUTPUT_FIELDS
    )
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
        print("[NODE: extract_facts]")
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
        print("[NODE: rewrite]")
        role = state.get("user_role", "neutral")
        question = state["question"]

        bias_keywords = ""
        if role == "defense":
            bias_keywords = "tình tiết giảm nhẹ, án treo, khung hình phạt thấp nhất"
        elif role == "victim":
            bias_keywords = "tình tiết tăng nặng, khung hình phạt cao nhất, bồi thường dân sự"

        system_msg = (
            "Bạn là chuyên gia Tìm kiếm Pháp lý Hình sự.\n"
            "Nhiệm vụ: Từ nội dung vụ án, tạo ra truy vấn ngắn gọn (tối đa 3–4 mệnh đề) "
            "để tìm kiếm ĐIỀU LUẬT ÁP DỤNG trong cơ sở dữ liệu Bộ luật Hình sự.\n\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. TẬP TRUNG vào: hành vi phạm tội cụ thể, đối tượng phạm tội, "
            "hậu quả/ tang vật, và loại tội danh.\n"
            "2. LOẠI BỎ hoàn toàn: ngày xét xử, số vụ án, tên tòa án, địa danh, "
            "tên bị cáo, nơi ở, thủ tục tố tụng.\n"
            "3. CHUẨN HÓA sang thuật ngữ pháp lý hình sự (ví dụ: 'tàng trữ trái phép "
            "chất ma túy', 'Ketamine', 'chất ma túy loại III').\n"
            f"4. THÊM từ khóa vai trò: {bias_keywords}\n\n"
            "VÍ DỤ OUTPUT TỐT: "
            "'tàng trữ trái phép chất ma túy Ketamine 1.623 gam tình tiết giảm nhẹ tái phạm'\n"
            "VÍ DỤ OUTPUT XẤU (cần tránh): "
            "'ngày 12 tháng 7 năm 2022 tòa án nhân dân quận Cầu Giấy xét xử sơ thẩm'\n\n"
            "OUTPUT: CHỈ xuất ra chuỗi truy vấn. Không giải thích. Không dấu chấm câu thừa."
        )
        response = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=f"NỘI DUNG VỤ ÁN:\n{question}")
        ])
        cleaned = response.content.strip().replace('"', '').replace("'", "")
        print(f"  Rewritten: {cleaned[:120]}")
        return {"question": cleaned, "retry_count": state.get("retry_count", 0) + 1}

    # NODE: RETRIEVE
    def retrieve_node(state: AgentState) -> dict:
        print("[NODE: retrieve]")
        try:
            docs = retriever.invoke(state["question"])
        except Exception as e:
            # Surface Milvus/embedding errors clearly instead of a silent 500.
            # Returning empty list lets grading fall back gracefully.
            print(f"[RETRIEVE ERROR] {type(e).__name__}: {e}")
            docs = []
        return {"documents": docs}

    # NODE: GRADE DOCUMENTS
    def grade_documents(state: AgentState) -> dict:
        print("[NODE: grade_documents]")
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
        print("[NODE: map_laws]")
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
        print("[NODE: generate]")
        case_details = state.get("full_case_content", state["question"])
        documents = state["documents"]
        role = state.get("user_role", "neutral")
        sentencing_data = state.get("sentencing_data", {})
        mapped_laws = state.get("mapped_laws", [])
        history = state.get("chat_history", [])

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

        # ─────────────────────────────────────────────────────────────────
        # ROLE-SPECIFIC PROMPT TEMPLATES
        # Each template has its own persona, output structure, and framing
        # ─────────────────────────────────────────────────────────────────
        if role == "defense":
            prompt_template = """{role_instruction}

Nhiệm vụ: Đọc kỹ hồ sơ vụ án và SOẠN LUẬN ĐIỂM BÀO CHỮA để giảm nhẹ tối đa hình phạt cho thân chủ.

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

LƯU Ý KHI BÀO CHỮA:
1. Ưu tiên tìm tình tiết giảm nhẹ (Điều 51 BLHS): thành khẩn, bồi thường, nhân thân tốt, phạm tội lần đầu.
2. Phân tích xem có thể đề nghị án treo không (án ≤ 3 năm + không tái phạm + có nơi cư trú ổn định).
3. Nếu có nhiều tội, đề xuất tách riêng hoặc giảm nhẹ từng tội.
4. Trích dẫn chính xác điều khoản luật để tăng tính thuyết phục.
5. Kiểm tra thời gian tạm giam để đề nghị khấu trừ.

QUY TRÌNH TƯ DUY (BẮT BUỘC):
BƯỚC 1: XÁC ĐỊNH tình tiết giảm nhẹ có lợi nhất.
BƯỚC 2: ĐỀ XUẤT định tội danh nhẹ nhất có thể lập luận được.
BƯỚC 3: TÍNH khung hình phạt thấp nhất + khấu trừ tạm giam.
BƯỚC 4: Đánh giá khả năng án treo.

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. LUẬN ĐIỂM BÀO CHỮA:**
1. **Về định tội danh:** (phân tích theo hướng có lợi cho bị cáo)
2. **Tình tiết giảm nhẹ đề xuất (Điều 51):**
   - (liệt kê từng tình tiết + căn cứ pháp lý)
3. **Phân tích nhân thân bị cáo:**

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BÀO CHỮA:**
1. Tội danh đề nghị: ...
2. Điều khoản áp dụng: ...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức thấp nhất trong khung, hoặc dưới khung nếu có căn cứ)
4. Đề nghị án treo / cải tạo không giam giữ (nếu đủ điều kiện)
5. Khấu trừ thời gian tạm giam: ...

**III. KHUYẾN NGHỊ CHO THÂN CHỦ:**
(Các bước cụ thể: bồi thường, viết đơn xin khoan hồng, xin giấy bãi nại, nộp án phí...)
"""
        elif role == "victim":
            prompt_template = """{role_instruction}

Nhiệm vụ: Đọc kỹ hồ sơ vụ án và SOẠN LUẬN ĐIỂM BẢO VỆ QUYỀN LỢI BỊ HẠI, yêu cầu xử nghiêm minh và bồi thường tối đa.

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

LƯU Ý KHI BẢO VỆ BỊ HẠI:
1. Tập trung làm rõ tình tiết tăng nặng (Điều 52 BLHS): có tổ chức, tái phạm, hậu quả nghiêm trọng.
2. Phân tích mức độ thiệt hại để yêu cầu bồi thường dân sự tối đa.
3. Phản bác các tình tiết giảm nhẹ mà bị cáo có thể viện dẫn.
4. Đề nghị mức án cao nhất trong khung có thể lập luận.
5. Yêu cầu tịch thu công cụ, phương tiện phạm tội.

QUY TRÌNH TƯ DUY (BẮT BUỘC):
BƯỚC 1: XÁC ĐỊNH tình tiết tăng nặng có thể áp dụng.
BƯỚC 2: ĐỀ XUẤT định tội danh và khung nặng nhất phù hợp.
BƯỚC 3: TÍNH thiệt hại thực tế để yêu cầu bồi thường.
BƯỚC 4: Đề nghị mức án cụ thể.

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. LUẬN ĐIỂM BẢO VỆ BỊ HẠI:**
1. **Về định tội danh:** (phân tích theo hướng tội nặng nhất có thể áp dụng)
2. **Tình tiết tăng nặng đề nghị áp dụng (Điều 52):**
   - (liệt kê từng tình tiết + căn cứ pháp lý)
3. **Mức độ thiệt hại và yêu cầu bồi thường:**

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BỊ HẠI:**
1. Tội danh đề nghị: ...
2. Điều khoản áp dụng: ...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức cao nhất trong khung phù hợp)
4. Không chấp nhận án treo (nếu có căn cứ)
5. TRÁCH NHIỆM DÂN SỰ: (yêu cầu bồi thường cụ thể)

**III. KHUYẾN NGHỊ CHO GIA ĐÌNH BỊ HẠI:**
(Hướng dẫn thu thập hóa đơn, chứng từ thiệt hại, yêu cầu cấp dưỡng, bảo vệ quyền lợi dài hạn...)
"""
        else:  # neutral — judge perspective
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
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
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
"""

        prompt = ChatPromptTemplate.from_template(prompt_template)
        
        # Biến đổi history thành dạng List[BaseMessage]
        history_msgs = []
        if history:
            # Lấy 4 tin nhắn gần nhất để tránh tràn context
            for msg in history[-4:]:
                if msg.get("role") == "user":
                    history_msgs.append(HumanMessage(content=msg.get("content", "")))
                else:
                    history_msgs.append(AIMessage(content=msg.get("content", "")))
        
        chain = prompt | llm | StrOutputParser()

        try:
            # Tạo prompt chính thức
            formatted_prompt = prompt.format_messages(
                role_instruction=role_instruction,
                context=context_text,
                case_details=case_details,
                deterministic_context=det_context,
                mapped_context=mapped_context,
            )
            
            # Nối lịch sử vào TRƯỚC prompt chính nhưng SAU system prompt (nếu có thể),
            # hoặc đơn giản là ghép tất cả lại. ChatPromptTemplate trả ra List[BaseMessage]
            final_messages = history_msgs + formatted_prompt
            
            response = llm.invoke(final_messages).content
        except Exception as e:
            return {"messages": [AIMessage(content=f"Lỗi xử lý: {e}")]}

        return {"messages": [AIMessage(content=response)]}

    # NODE: REBUTTAL
    def rebuttal_node(state: AgentState) -> dict:
        """Generate legally sound counter-argument against provided argument."""
        print("[NODE: rebuttal]")
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
    # INTENT CLASSIFICATION — 3-way router
    # -------------------------------------------------------
    def classify_intent(state: AgentState) -> str:
        """
        Route messages into one of 3 paths:
          'casual'   — greeting, chit-chat, off-topic → simple canned response
          'followup' — elaboration/question about a prior AI response
          'new_case' — penal law case or legal question → full RAG pipeline
        """
        history = state.get("chat_history", []) or []
        question = state["question"].strip()

        # Fast rule: very short input with no legal keywords → likely casual
        LEGAL_KEYWORDS = ["điều", "khoản", "bộ luật", "tội", "hình phạt", "bị cáo",
                          "bị hại", "tòa án", "viện kiểm sát", "ngày", "năm", "tháng",
                          "tạm giam", "xét xử", "phạt", "án", "hành vi", "law", "penal"]
        is_short = len(question) < 120
        has_legal = any(kw in question.lower() for kw in LEGAL_KEYWORDS)

        if is_short and not has_legal and not history:
            print(f"  [INTENT] Short + no legal keywords + no history → casual")
            return "casual"

        # Long input is almost certainly a new case dump
        if len(question) > 500:
            print("  [INTENT] Long input → new_case")
            return "new_case"

        # No history → first message, send to full pipeline
        if not history:
            print("  [INTENT] No history → new_case")
            return "new_case"

        classification_prompt = (
            "Bạn là bộ phân loại đầu vào cho một hệ thống chatbot pháp luật hình sự Việt Nam.\n"
            f"Lịch sử hội thoại có {len(history)} tin nhắn.\n"
            f"Tin nhắn mới của người dùng: \"{question[:400]}\"\n\n"
            "Phân loại tin nhắn này thành MỘT trong ba loại:\n"
            "- \"casual\": Chào hỏi, hỏi chatbot là gì, nói chuyện phiếm, hoặc nội dung "
            "HOÀN TOÀN không liên quan đến pháp luật hình sự.\n"
            "- \"followup\": Hỏi thêm, yêu cầu giải thích, phân tích lại điểm cụ thể, "
            "cung cấp thêm thông tin mới để AI xem xét lại — LIÊN QUAN đến phân tích AI đã trả lời.\n"
            "- \"new_case\": Hồ sơ vụ án mới hoàn toàn hoặc câu hỏi pháp lý mới "
            "không liên quan đến cuộc hội thoại hiện tại.\n\n"
            "Chỉ trả về đúng một từ: \"casual\", \"followup\", hoặc \"new_case\"."
        )
        try:
            result = llm.invoke([HumanMessage(content=classification_prompt)]).content.strip().lower()
            if "casual" in result:
                intent = "casual"
            elif "followup" in result:
                intent = "followup"
            else:
                intent = "new_case"
        except Exception:
            intent = "new_case"  # fail-safe

        print(f"  [INTENT] → {intent} | query='{question[:80]}'")
        return intent

    # NODE: CASUAL RESPOND
    def casual_respond(state: AgentState) -> dict:
        """Handle greetings, off-topic, and unrelated queries."""
        print("[NODE: casual_respond]")
        question = state["question"].strip().lower()

        # Detect greeting
        GREETINGS = ["hi", "hello", "chào", "xin chào", "hey", "helo", "ola"]
        is_greeting = any(q in question for q in GREETINGS) or len(question) <= 10

        if is_greeting:
            reply = (
                "Xin chào! Tôi là **Trợ lý Pháp luật Hình sự AI**.\n\n"
                "Tôi có thể giúp bạn:\n"
                "- **Phân tích vụ án hình sự** — định tội danh, lượng hình\n"
                "- **Nhận định của tòa án** hoặc lập luận theo vai trò **bào chữa / bị hại**\n"
                "- **Trích dẫn Bộ luật Hình sự** điều khoản liên quan\n"
                "- **Giải thích chi tiết** bất kỳ điểm nào trong phân tích\n\n"
                "Hãy dán nội dung hồ sơ vụ án hoặc đặt câu hỏi pháp lý để bắt đầu!"
            )
        else:
            reply = (
                "Xin lỗi, tôi chỉ có thể hỗ trợ các vấn đề liên quan đến **pháp luật hình sự Việt Nam**.\n\n"
                "Nếu bạn có hồ sơ vụ án hoặc câu hỏi về tội danh, khung hình phạt, "
                "hay tình tiết tăng nặng/giảm nhẹ, hãy cho tôi biết nhé!"
            )

        return {"messages": [AIMessage(content=reply)]}

    # NODE: FOLLOW-UP GENERATE
    def followup_generate(state: AgentState) -> dict:
        print("[NODE: followup_generate]")
        question = state["question"]
        role = state.get("user_role", "neutral")
        history = state.get("chat_history", []) or []
        documents = state.get("documents", []) or []

        # Use documents that were retrieved earlier in session (if any)
        context = ""
        if documents:
            context = "\n\n".join([
                f"[{d.metadata.get('article_number', '?')}] {d.page_content[:600]}"
                for d in documents[:6]
            ])

        role_persona = {
            "defense": "Bạn là luật sư bào chữa đang bảo vệ quyền lợi tối đa cho bị cáo.",
            "victim":  "Bạn là luật sư bảo vệ quyền lợi bị hại, yêu cầu xử lý nghiêm minh.",
            "neutral": "Bạn là một chuyên gia pháp lý trung lập, phân tích khách quan.",
        }.get(role, "Bạn là chuyên gia pháp lý.")

        system_prompt = (
            f"{role_persona}\n"
            "Nhiệm vụ: Trả lời câu hỏi tiếp theo của người dùng dựa trên cuộc hội thoại đang diễn ra.\n\n"
            "QUY TẮC:\n"
            "1. Trả lời trực tiếp, cụ thể về đúng điểm người dùng hỏi.\n"
            "2. Trích dẫn điều khoản luật cụ thể nếu cần.\n"
            "3. Nếu người dùng đưa ra thêm thông tin/chứng cứ → xem xét lại và cập nhật nhận định.\n"
            "4. Nếu người dùng yêu cầu giải thích rõ hơn → giải thích chi tiết, dễ hiểu.\n"
            "5. Giữ nhất quán với vai trò và lập luận đã trình bày trước đó.\n"
        )
        if context:
            system_prompt += f"\nVĂN BẢN LUẬT THAM CHIẾU:\n{context}\n"

        # Build conversation: history + current question
        history_msgs = []
        for msg in history[-8:]:  # last 8 msgs for context
            if msg.get("role") == "user":
                history_msgs.append(HumanMessage(content=msg["content"]))
            else:
                history_msgs.append(AIMessage(content=msg["content"]))

        all_messages = [
            SystemMessage(content=system_prompt),
            *history_msgs,
            HumanMessage(content=question),
        ]

        try:
            response = llm.invoke(all_messages).content
        except Exception as e:
            response = f"Lỗi xử lý câu hỏi: {e}"

        return {"messages": [AIMessage(content=response)]}

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
    workflow.add_node("followup", followup_generate)
    workflow.add_node("casual", casual_respond)   # ← new casual path

    # START → 3-way intent router
    workflow.add_conditional_edges(
        START,
        classify_intent,
        {"new_case": "rewrite", "followup": "followup", "casual": "casual"}
    )
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
    workflow.add_edge("followup", END)
    workflow.add_edge("casual", END)   # ← casual path ends here

    app_compiled = workflow.compile()
    # BUG-14 FIX: Store llm in app_state so /practice/evaluate can reuse it
    # instead of creating a new ChatOpenAI client on every request.
    app_state["llm"] = llm
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
        "is_relevant": None,
        "rebuttal_against": req.rebuttal_against,
        "chat_history": req.conversation_history,
    }

    try:
        output = await graph.ainvoke(inputs)
        final_answer = output["messages"][-1].content

        # Sanitize mapped_laws: replace any None field values with "" to
        # avoid Pydantic validation errors when no legal content was found
        raw_laws = output.get("mapped_laws") or []
        clean_laws = [
            {k: (v if v is not None else "") for k, v in law.items()}
            for law in raw_laws
        ] or None

        return PredictResponse(
            result=final_answer,
            extracted_facts=output.get("extracted_facts"),
            mapped_laws=clean_laws,
            sentencing_data=output.get("sentencing_data"),
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[PREDICT ERROR] {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)


# ===========================================================
# PRACTICE / EVALUATE ENDPOINT
# ===========================================================
@app.post("/practice/evaluate", response_model=PracticeEvalResponse)
async def practice_evaluate(req: PracticeEvalRequest):
    """
    Evaluate a user's legal analysis from the perspective of the chosen role.
    Returns a score (0–100) plus structured feedback.
    """
    # BUG-03 FIX: Reuse the llm from app_state instead of creating a new
    # ChatOpenAI instance on every request. A new client per-request is:
    #   (1) wasteful — re-initializes the HTTP client each time, and
    #   (2) silently broken — if OPENROUTER_API_KEY is not set, the client is
    #       constructed without error but fails only when .invoke() is called.
    llm_instance = app_state.get("llm")
    if not llm_instance:
        raise HTTPException(status_code=503, detail="Model not loaded — service is still starting up")

    role_label = {
        "neutral": "Thẩm phán (trung lập)",
        "defense": "Luật sư bào chữa (bảo vệ bị cáo)",
        "victim": "Luật sư bảo vệ bị hại",
    }.get(req.user_mode, "Chuyên gia pháp lý")

    role_criteria = {
        "neutral": """
- Đánh giá khả năng xác định tội danh đúng điều luật.
- Kiểm tra việc phân tích tình tiết tăng nặng/giảm nhẹ theo Điều 51, 52 BLHS.
- Kiểm tra việc lượng hình (mức phạt hợp lý, tổng hợp theo Điều 55 nếu nhiều tội).
- Kiểm tra xem đã trừ thời gian tạm giam chưa.
- Đánh giá tính trung lập, khách quan của phân tích.
""",
        "defense": """
- Đánh giá khả năng tìm và chưξng dẫn tình tiết giảm nhẹ (có theo Điều 51 không).
- Kiểm tra đề xuất án treo hoặc cải tạo không giam giữ (có căn cứ pháp lý không).
- Đánh giá luận điểm bào chữa: có bác bỏ tình tiết tăng nặng hiệu quả không.
- Kiểm tra đề nghị khấu trừ thời gian tạm giam.
- Đánh giá có xuất hiện yêu cầu giảm nhẹ tội danh không.
""",
        "victim": """
- Đánh giá khả năng xác định tình tiết tăng nặng (có theo Điều 52 không).
- Kiểm tra yêu cầu bồi thường dân sự: có căn cứ thiệt hại không.
- Đánh giá luận điểm phản bác tình tiết giảm nhẹ của bị cáo.
- Kiểm tra đề nghị mức án cao nhất có căn cứ không.
- Đánh giá có yêu cầu tịch thu công cụ, vật chứng không.
""",
    }.get(req.user_mode, "")

    eval_prompt = f"""Bạn là giáo sư luật hình sự Việt Nam đang chấm bài làm của sinh viên luật.

VU ÁN:
{req.case_description}

VAI TRÒ NGƯỜI DÙNG: {role_label}

PHÂN TÍCH CỦA NGƯỜI DÙNG:
{req.user_analysis}

TIÊU CHÍ ĐÁNH GIÁ (theo vai trò {role_label}):
{role_criteria}

NHIỆM VỤ: Đánh giá chất lượng phân tích pháp lý trên. Chấm điểm từ 0 đến 100.

Trả về JSON hợp lệ (không markdown, không giải thích bên ngoài JSON) theo cấu trúc sau:
{{
  "score": <số nguyên từ 0 đến 100>,
  "feedback": {{
    "strengths": ["<điểm mạnh 1>", "<điểm mạnh 2>", ...],
    "improvements": ["<cần cải thiện 1>", "<cần cải thiện 2>", ...],
    "missed_articles": ["<Điều X BLHS (ý nghĩa)>", ...],
    "suggestion": "<Gợi ý tổng hợp cho người dùng>"
  }}
}}

Quy tắc:
- strengths: 2–4 điểm tích cực cụ thể (trích dẫn rõ luận điểm người dùng).
- improvements: 2–5 điểm cần cải thiện, cụ thể, có đều khoản tham chiếu.
- missed_articles: liệt kê các điều luật quan trọng mà người dùng bỏ sót (có thể rỗng nếu đầy đủ).
- suggestion: 1–2 câu gợi ý cụ thể nhất cho người học.
OUTPUT: CHỈ JSON."""

    try:
        response = llm_instance.invoke([HumanMessage(content=eval_prompt)])
        raw = response.content.strip()
        raw = re.sub(r"```json?\s*", "", raw).strip("`").strip()
        data = json.loads(raw)

        feedback = data.get("feedback", {})
        return PracticeEvalResponse(
            score=int(data.get("score", 50)),
            feedback=PracticeEvalFeedback(
                strengths=feedback.get("strengths", []),
                improvements=feedback.get("improvements", []),
                missed_articles=feedback.get("missed_articles", []),
                suggestion=feedback.get("suggestion", ""),
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Practice evaluation failed: {e}")
