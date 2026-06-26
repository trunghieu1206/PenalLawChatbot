"""
graph/nodes/generation.py — VNPLaw AI Service
Nodes: generate, answer_verify, practice_evaluate, casual_respond,
       followup_generate, and the classify_intent router.
"""
import json
import time
from typing import List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document

from app.core.schemas import AgentState
from app.graph.nodes.verify import (
    _ARTICLE_CITE_PAT,
    _verify_no_hallucinated_articles,
    _verify_temporal_validity,
    _verify_role_signal,
)
from app.utils.dates import _edition_for_date
from app.utils.legal import _extract_json
from app.utils.text import sanitize_text, _sanitize_msgs, cleanup_response


# ─────────────────────────────────────────────────────────────────────────────
# INTENT ROUTER  (pure function — no LLM dependency)
# ─────────────────────────────────────────────────────────────────────────────

CASUAL_PHRASES = [
    "xin chào", "chào bạn", "hi", "hello", "hey", "helo", "ola",
    "bạn là ai", "bạn là gì", "chatbot là gì", "cảm ơn", "thank",
    "ok bạn", "được rồi", "bye", "tạm biệt", "hẹn gặp",
]

LEGAL_KEYWORDS = [
    # Legal acts
    "điều", "khoản", "bộ luật", "tội", "hình phạt", "hành vi", "án",
    "phạt", "tù", "phạm tội", "phạm tội", "ngày", "năm", "tháng",
    # Actors
    "bị cáo", "bị hại", "nạn nhân", "bị can", "nghi phạm", "thủ phạm",
    "luật sư", "viện kiểm sát", "tòa án", "công an", "cảnh sát", "thẩm phán",
    # Criminal acts (Vietnamese)
    "giết", "đánh", "chém", "bắn", "cướp", "trộm", "lừa đảo", "hiếp",
    "tống tiền", "bắt cóc", "đốt", "buôn bán", "ma túy", "mua bán",
    "tàng trữ", "sản xuất", "vận chuyển", "chiếm đoạt", "xâm phạm",
    "gây thương tích", "tham nhũng", "hối lộ", "trốn thuế", "gian lận",
    # Legal process
    "tạm giam", "xét xử", "khởi tố", "điều tra", "truy tố", "kết án",
    "bắt giữ", "khám xét", "thu giữ", "tang vật", "biên bản",
    # Sentencing
    "tình tiết", "giảm nhẹ", "tăng nặng", "án treo", "cải tạo",
    "chung thân", "tử hình", "bồi thường", "tịch thu",
    # English fallback
    "law", "penal", "crime", "criminal", "offense", "sentence",
]

FOLLOWUP_PHRASES = [
    "giải thích thêm", "tại sao", "vì sao", "thế còn", "thế nếu",
    "còn điều", "điều đó có nghĩa", "bạn vừa nói", "ý bạn là",
    "phân tích thêm", "nói rõ hơn", "chi tiết hơn", "ví dụ",
    "như vậy thì", "trong trường hợp", "nếu bị cáo", "nếu nạn nhân",
    "why", "what if", "can you explain", "elaborate", "clarify",
    "you said", "earlier you", "in that case",
]


def classify_intent(state: AgentState) -> str:
    """
    Route messages into one of 3 paths:
      'casual'   — greeting, chit-chat, off-topic → simple canned response
      'followup' — elaboration/question about a prior AI response
      'new_case' — penal law case or legal question → full RAG pipeline

    Practice Mode always routes as 'new_case'.
    """
    # Practice Mode bypass
    if state.get("is_practice_mode"):
        print("  [INTENT] Practice Mode → new_case (bypass heuristics)")
        return "new_case"

    history  = state.get("chat_history", []) or []
    question = state["question"].strip()
    q_lower  = question.lower()

    # Layer 2a: Greeting / casual fast-path
    if any(phrase in q_lower for phrase in CASUAL_PHRASES) and len(question) < 80:
        print(f"  [INTENT] Greeting phrase detected → casual | query='{question[:60]}'")
        return "casual"

    # Layer 2b: Legal keyword detection
    has_legal = any(kw in q_lower for kw in LEGAL_KEYWORDS)

    # Layer 2c: No legal keywords and no history → casual
    if len(question) < 120 and not has_legal and not history:
        print("  [INTENT] Short + no legal keywords + no history → casual")
        return "casual"

    # Layer 2d: Long input with legal keywords → new_case
    if len(question) > 400 and has_legal:
        print(f"  [INTENT] Long ({len(question)} chars) + legal keywords → new_case")
        return "new_case"

    # Very long input regardless of keywords
    if len(question) > 600:
        print("  [INTENT] Very long input → new_case (no keyword check)")
        return "new_case"

    # Layer 2e: No history → treat as new case
    if not history:
        print("  [INTENT] No history → new_case")
        return "new_case"

    # Layer 2f: Follow-up phrase fast-path
    if any(phrase in q_lower for phrase in FOLLOWUP_PHRASES) and history:
        print(f"  [INTENT] Follow-up phrase detected → followup | query='{question[:60]}'")
        return "followup"

    # Layer 3: Deterministic fallback
    if history:
        intent = "followup"
        print("  [INTENT] No fast-path matched + has history → followup (default)")
    else:
        intent = "new_case"
        print("  [INTENT] No fast-path matched + no history → new_case (default)")

    print(f"  [INTENT] → {intent} | query='{question[:80]}'")
    return intent


def application_mode_router(state: AgentState) -> str:
    """Router: after map_laws, decide generate vs practice_evaluate."""
    if state.get("is_practice_mode"):
        print("  [ROUTER: application_mode] practice_evaluate")
        return "practice_evaluate"
    print("  [ROUTER: application_mode] generate")
    return "generate"


# ─────────────────────────────────────────────────────────────────────────────
# FACTORY: make_generation_nodes
# Returns a dict of node callables, all bound to the injected 'llm'.
# ─────────────────────────────────────────────────────────────────────────────

def make_generation_nodes(llm, bm25_index, bm25_docs, retriever, measure_time):
    """
    Factory that returns:
      generate_node, answer_verify_node, practice_evaluate_node,
      casual_respond_node, followup_generate_node
    all bound to the provided llm/bm25/retriever dependencies.
    """

    # ── Helper: looks_like_case (mirrors classify_intent heuristics) ──────────
    def _looks_like_case(text: str) -> bool:
        q = text.strip()
        q_lower = q.lower()
        if any(p in q_lower for p in CASUAL_PHRASES) and len(q) < 80:
            return False
        has_legal = any(kw in q_lower for kw in LEGAL_KEYWORDS)
        if len(q) > 400 and has_legal:
            return True
        if len(q) > 600:
            return True
        if len(q) < 120 and not has_legal:
            return False
        return has_legal

    # ─────────────────────────────────────────────────────────────────────
    # NODE: GENERATE
    # ─────────────────────────────────────────────────────────────────────
    @measure_time('generate')
    def generate_node(state: AgentState) -> dict:
        """Generate a legal analysis response based on role and retrieved context."""
        print("[NODE: generate]")
        role        = state.get("user_role", "neutral")
        facts       = state.get("extracted_facts") or {}
        mapped_laws = state.get("mapped_laws") or []
        documents   = state.get("documents") or []
        history     = state.get("chat_history") or []
        case_details = state.get("full_case_content", state.get("question", ""))

        # ── Build context text from temporally-tagged documents ───────────────
        primary_docs    = [d for d in documents if d.metadata.get("_temporal_role") == "primary"]
        comparison_docs = [d for d in documents if d.metadata.get("_temporal_role") == "comparison"]
        adjustment_docs = [d for d in documents if d.metadata.get("_temporal_role") == "adjustment"]
        ordered_docs    = primary_docs + comparison_docs + adjustment_docs

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"vai_trò={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in ordered_docs
        ]))

        # ── Build mapped_laws context ─────────────────────────────────────────
        mapped_context = ""
        if mapped_laws and not (len(mapped_laws) == 1 and mapped_laws[0].get("_mapping_error")):
            lines = ["**Các tội danh đã xác định:**"]
            for law in mapped_laws:
                err = law.get("_mapping_error", False)
                lines.append(
                    f"- {law.get('article','?')} {law.get('clause','?')}: "
                    f"{law.get('offense_name','?')} "
                    f"[{law.get('edition_applied','?')}]"
                    + (" ⚠️ (ánh xạ có thể không chính xác)" if err else "")
                )
            mapped_context = "\n".join(lines)
        else:
            mapped_context = "**Lưu ý:** Hệ thống không thể xác định tội danh cụ thể từ thông tin đã cung cấp."

        # ── Build deterministic sentencing context ────────────────────────────
        sentencing_data = state.get("sentencing_data") or {}
        det_lines = []
        if sentencing_data.get("detention_months") is not None:
            det_lines.append(
                f"- Thời gian tạm giam đã tính: {sentencing_data['detention_months']} tháng"
            )
        if sentencing_data.get("defendant_age_at_crime") is not None:
            minor_def = sentencing_data.get("defendant_is_minor", False)
            det_lines.append(
                f"- Tuổi bị cáo tại thời điểm phạm tội: {sentencing_data['defendant_age_at_crime']} tuổi"
                + (" (DƯỚI 18 TUỔI — BẮT BUỘC áp dụng Chương XII BLHS)" if minor_def else "")
            )
        if sentencing_data.get("victim_age_at_crime") is not None:
            minor_vic = sentencing_data.get("victim_is_minor", False)
            det_lines.append(
                f"- Tuổi nạn nhân tại thời điểm phạm tội: {sentencing_data['victim_age_at_crime']} tuổi "
                f"({'DƯỚI 18 TUỔI' if minor_vic else 'TRÊN 18 TUỔI'})"
            )
        det_context = (
            "\n\nDỮ LIỆU ĐÃ TÍNH TOÁN CHÍNH XÁC (BẮT BUỘC SỬ DỤNG):\n" + "\n".join(det_lines)
            if det_lines else ""
        )

        # ── Build nhân thân context ───────────────────────────────────────────
        nhan_than_lines = []
        if facts.get("co_tien_an") is True:
            nhan_than_lines.append(
                "- **Tiền án:** CÓ TIỀN ÁN chưa xóa án tích "
                "(có thể là tình tiết tăng nặng / tái phạm / tái phạm nguy hiểm — "
                "cần phân tích chi tiết theo Điều 48/52 và 49/53)."
            )
        elif facts.get("co_tien_an") is False:
            nhan_than_lines.append(
                "- **Tiền án:** KHÔNG CÓ tiền án chính thức (theo trích lục hồ sơ). "
                "Nếu hồ sơ có mục 'Nhân thân' liệt kê bản án cũ đã xóa án tích, "
                "AI được phép đề cập đây là 'nhân thân xấu' — đây là phân tích ĐÚNG, "
                "KHÔNG phải mâu thuẫn."
            )
        if facts.get("da_boi_thuong") is True:
            nhan_than_lines.append(
                "- **Bồi thường:** ĐÃ bồi thường thiệt hại "
                "→ tình tiết giảm nhẹ (Điều 46/51)."
            )
        if facts.get("da_thanh_khan_khai_bao") is True:
            nhan_than_lines.append(
                "- **Thành khẩn:** ĐÃ thành khẩn khai báo "
                "→ tình tiết giảm nhẹ (Điều 46/51)."
            )
        nhan_than_context = (
            "**Nhân thân bị cáo:**\n" + "\n".join(nhan_than_lines)
            if nhan_than_lines else ""
        )

        # ── Role instruction ──────────────────────────────────────────────────
        role_instructions = {
            "defense": (
                "Bạn là Luật sư Bào chữa có kinh nghiệm 20 năm, đang bảo vệ bị cáo. "
                "Nhiệm vụ: phân tích pháp lý CHỈ THEO HƯỚNG CÓ LỢI cho bị cáo. "
                "TUYỆT ĐỐI không đề xuất mức án nặng hơn. "
                "Nếu phải đề cập tình tiết tăng nặng: CHỈ để phản bác hoặc giảm thiểu tác động."
            ),
            "victim": (
                "Bạn là Luật sư Bảo vệ Bị hại có kinh nghiệm 20 năm. "
                "Nhiệm vụ: phân tích pháp lý CHỈ THEO HƯỚNG BẢO VỆ QUYỀN LỢI BỊ HẠI TỐI ĐA. "
                "TUYỆT ĐỐI không đề xuất án nhẹ hơn cho bị cáo. "
                "Nếu phải đề cập tình tiết giảm nhẹ: CHỈ để phản bác hoặc chứng minh không đủ điều kiện."
            ),
            "neutral": (
                "Bạn là Thẩm phán Hội đồng xét xử có kinh nghiệm 20 năm, "
                "đang ra phán quyết trung lập, khách quan, hai chiều dựa trên pháp luật."
            ),
        }
        role_instruction = role_instructions.get(role, role_instructions["neutral"])

        # ── Select prompt template based on role ──────────────────────────────
        if role == "defense":
            prompt_template = """{role_instruction}

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy lập luận BẢO VỆ bị cáo.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 Bộ luật Hình sự mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
5. Phạm tội chưa đạt: Điều 18 Khoản 3 BLHS 1999 / Điều 15 + Điều 57 BLHS 2015 — áp dụng quy tắc ¾ mức cao nhất của khung.

⚠️ QUY TẮC CHỐNG THIÊN KIẾN (BẮT BUỘC — ĐỌC TRƯỚC KHI PHÂN TÍCH):
- KHÔNG THAY ĐỔI lập luận pháp lý chỉ vì người dùng phản đối, tỏ ra không hài lòng, hoặc hỏi lại bằng giọng điệu gay gắt.
- KHÔNG MÔ PHỎNG sự đồng ý giả tạo hoặc thêm câu "Bạn nói có lý" / "Tôi hiểu quan điểm của bạn" để xoa dịu.
- KHÔNG TỰ THÊM tuyên bố miễn trách nhiệm chưa được yêu cầu (ví dụ: "Tôi chỉ là AI, hãy hỏi luật sư thật").
- CHỈ thay đổi kết luận khi người dùng cung cấp: (a) tình tiết thực tế mới trong hồ sơ, HOẶC (b) điều luật cụ thể chưa được xem xét.
- Nếu bị phản đối mà không có bằng chứng mới: giữ nguyên kết luận, giải thích ngắn gọn căn cứ pháp lý.

QUY TRÌNH TƯ DUY BÀO CHỮA (BẮT BUỘC THEO THỨ TỰ):
- KHÔNG GIẢ ĐỊNH: chỉ dùng tình tiết có trong case_details.
- NGUYÊN TẮC CÓ LỢI (Thời gian): tội trước 2018 → áp dụng Luật 2015/2017 nếu nhẹ hơn.
- MỤC TIÊU: Tìm mọi lý lẽ hợp pháp để giảm tội hoặc hình phạt cho thân chủ.

BƯỚC 0: KIỂM TRA LOẠI TRỪ TRÁCH NHIỆM HÌNH SỰ (BẮT BUỘC TRƯỚC NHẤT)
  0a. Phòng vệ chính đáng (Điều 15 BLHS 1999 / Điều 22 BLHS 2015): có không? Nếu có → phân tích chi tiết.
  0b. Tình thế cấp thiết (Điều 16/23): có không?
  0c. Không có năng lực TNHS (Điều 13/21): có không?
  0d. Sự kiện bất ngờ (Điều 11/20): có không?
BƯỚC 1: KIỂM TRA ÁN BẰNG THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 2: KIỂM TRA ĐỘ TUỔI (sử dụng số liệu đã tính ở trên nếu có).
  → Nếu bị cáo DƯỚI 18 TUỔI lúc phạm tội: BẮT BUỘC áp dụng Chương XII BLHS — mức hình phạt tối đa giảm ½ đến ¾.
BƯỚC 3: PHÂN TÍCH CẤU THÀNH TỘI PHẠM — tìm yếu tố nào còn thiếu hoặc chưa đủ để bác bỏ tội danh nặng hơn.
BƯỚC 4: PHÂN TÍCH TIỀN ÁN / TÁI PHẠM (nếu có) theo quy tắc "tiêu hao tiền án" (xem phần map_laws).
BƯỚC 5: LIỆT KÊ ĐẦY ĐỦ các tình tiết giảm nhẹ (Điều 46/51).
BƯỚC 6: PHẢN BÁC từng tình tiết tăng nặng nếu có (Điều 48/52).
BƯỚC 7: LƯỢNG HÌNH — đề xuất mức hình phạt thấp nhất có thể biện hộ được.
  → Xem xét dưới khung (Điều 47/54) nếu ≥ 2 tình tiết giảm nhẹ và không tăng nặng.
  → Xem xét miễn hình phạt (Điều 25/59) nếu trường hợp đặc biệt.
  → Đề xuất án treo (Điều 60/65) nếu đủ 5 điều kiện.
BƯỚC 8: TỔNG HỢP HÌNH PHẠT (Điều 50/55) nếu nhiều tội.
BƯỚC 9: KHẤU TRỪ THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. PHÂN TÍCH PHÁP LÝ (GÓC ĐỘ BÀO CHỮA):**
1. **Kiểm tra loại trừ TNHS:** (phòng vệ chính đáng, tình thế cấp thiết, sự kiện bất ngờ...)
2. **Phân tích cấu thành tội phạm:** (có yếu tố nào còn thiếu không?)
3. **Tình tiết giảm nhẹ (đầy đủ):** (Điều 46/51 — liệt kê tất cả)
4. **Phản bác tình tiết tăng nặng:** (nếu có)
5. **Giai đoạn phạm tội:** (hoàn thành / chưa đạt — ảnh hưởng mức án)
6. **Vai trò đồng phạm:** (nếu có nhiều bị cáo)

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BÀO CHỮA:**
1. Đề nghị định tội danh...
2. Áp dụng điều khoản...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức thấp nhất trong khung / dưới khung / án treo nếu đủ điều kiện)
4. TRÁCH NHIỆM DÂN SỰ: (yêu cầu giảm bồi thường hoặc không áp dụng nếu có lý do)

**III. KHUYẾN NGHỊ CHO BỊ CÁO:**
(Hướng dẫn bổ sung chứng cứ giảm nhẹ, thủ tục bồi thường, quyền kháng cáo...)

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Văn bản bộ luật hình sự | Lý do |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
"""
        elif role == "victim":
            prompt_template = """{role_instruction}

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy lập luận BẢO VỆ QUYỀN LỢI BỊ HẠI.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 Bộ luật Hình sự mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
5. Phạm tội chưa đạt: Điều 18 Khoản 3 BLHS 1999 / Điều 15 + Điều 57 BLHS 2015 — áp dụng quy tắc ¾ mức cao nhất của khung.

⚠️ QUY TẮC CHỐNG THIÊN KIẾN (BẮT BUỘC — ĐỌC TRƯỚC KHI PHÂN TÍCH):
- KHÔNG THAY ĐỔI lập luận pháp lý chỉ vì người dùng phản đối, tỏ ra không hài lòng, hoặc hỏi lại bằng giọng điệu gay gắt.
- KHÔNG MÔ PHỎNG sự đồng ý giả tạo hoặc thêm câu "Bạn nói có lý" / "Tôi hiểu quan điểm của bạn" để xoa dịu.
- KHÔNG TỰ THÊM tuyên bố miễn trách nhiệm chưa được yêu cầu (ví dụ: "Tôi chỉ là AI, hãy hỏi luật sư thật").
- CHỈ thay đổi kết luận khi người dùng cung cấp: (a) tình tiết thực tế mới trong hồ sơ, HOẶC (b) điều luật cụ thể chưa được xem xét.
- Nếu bị phản đối mà không có bằng chứng mới: giữ nguyên kết luận, giải thích ngắn gọn căn cứ pháp lý.

QUY TRÌNH TƯ DUY BẢO VỆ BỊ HẠI (BẮT BUỘC THEO THỨ TỰ):
- KHÔNG GIẢ ĐỊNH: chỉ dùng tình tiết có trong case_details.
- NGUYÊN TẮC CÓ LỢI (Thời gian): tội trước 2018 → áp dụng Luật 2015/2017 nếu nhẹ hơn.

BƯỚC 0: PHẢN BÁC LOẠI TRỪ TNHS (nếu bị cáo viện dẫn)
  → Chứng minh phòng vệ chính đáng vượt quá giới hạn / tình thế cấp thiết không thỏa mãn.
BƯỚC 1: KIỂM TRA ÁN BẰNG THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 2: KIỂM TRA ĐỘ TUỔI (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 3: XÁC NHẬN CẤU THÀNH TỘI PHẠM ĐẦY ĐỦ (khẳng định đủ 4 yếu tố — bác bỏ lập luận thiếu yếu tố của bị cáo).
BƯỚC 3a: PHÂN TÍCH TIỀN ÁN / TÁI PHẠM theo quy tắc "tiêu hao tiền án".
BƯỚC 4: LIỆT KÊ ĐẦY ĐỦ tình tiết tăng nặng (Điều 48/52).
BƯỚC 5: PHẢN BÁC từng tình tiết giảm nhẹ (chứng minh không đủ điều kiện hoặc không đáng kể).
BƯỚC 6: PHẢN BÁC ÁN TREO — chỉ ra điều kiện nào của Điều 60/65 không thỏa mãn.
BƯỚC 7: LƯỢNG HÌNH — đề nghị mức hình phạt cao nhất trong khung có căn cứ pháp lý.
BƯỚC 8: TÍNH TOÁN BỒI THƯỜNG DÂN SỰ đầy đủ (vật chất + tinh thần + phát sinh).
BƯỚC 9: YÊU CẦU HÌNH PHẠT BỔ SUNG (tịch thu, cấm chức vụ, phạt tiền bổ sung nếu phù hợp).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. PHÂN TÍCH PHÁP LÝ (GÓC ĐỘ BẢO VỆ BỊ HẠI):**
1. **Xác nhận cấu thành tội phạm:** (khẳng định đủ 4 yếu tố)
2. **Tình tiết tăng nặng (đầy đủ):** (Điều 48/52 — liệt kê tất cả)
3. **Phản bác tình tiết giảm nhẹ:** (nếu có)
4. **Phản bác án treo:** (chỉ ra điều kiện không thỏa mãn)

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BẢO VỆ BỊ HẠI:**
1. Đề nghị tuyên bố bị cáo phạm tội...
2. Áp dụng điều khoản...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức cao nhất trong khung)
4. TRÁCH NHIỆM DÂN SỰ: (yêu cầu bồi thường đầy đủ — liệt kê từng khoản)
5. HÌNH PHẠT BỔ SUNG: (nếu áp dụng được)

**III. KHUYẾN NGHỊ CHO GIA ĐÌNH BỊ HẠI:**
(Hướng dẫn thu thập hóa đơn, chứng từ thiệt hại, yêu cầu cấp dưỡng, bảo vệ quyền lợi dài hạn...)

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Văn bản bộ luật hình sự | Lý do |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
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
{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 Bộ luật Hình sự mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
5. Phạm tội chưa đạt: Điều 18 Khoản 3 BLHS 1999 / Điều 15 + Điều 57 BLHS 2015 — áp dụng quy tắc ¾ mức cao nhất của khung.

⚠️ QUY TẮC CHỐNG THIÊN KIẾN (BẮT BUỘC — ĐỌC TRƯỚC KHI PHÂN TÍCH):
- KHÔNG THAY ĐỔI lập luận pháp lý chỉ vì người dùng phản đối, tỏ ra không hài lòng, hoặc hỏi lại bằng giọng điệu gay gắt.
- KHÔNG MÔ PHỎNG sự đồng ý giả tạo hoặc thêm câu "Bạn nói có lý" / "Tôi hiểu quan điểm của bạn" để xoa dịu.
- KHÔNG TỰ THÊM tuyên bố miễn trách nhiệm chưa được yêu cầu (ví dụ: "Tôi chỉ là AI, hãy hỏi luật sư thật").
- CHỈ thay đổi kết luận khi người dùng cung cấp: (a) tình tiết thực tế mới trong hồ sơ, HOẶC (b) điều luật cụ thể chưa được xem xét.
- Nếu bị phản đối mà không có bằng chứng mới: giữ nguyên kết luận, giải thích ngắn gọn căn cứ pháp lý.

QUY TRÌNH TƯ DUY LƯỢNG HÌNH (BẮT BUỘC THEO THỨ TỰ):
- KHÔNG GIẢ ĐỊNH: chỉ dùng tình tiết có trong case_details.
- NGUYÊN TẮC CÓ LỢI (Thời gian): tội trước 2018 → áp dụng Luật 2015/2017 nếu nhẹ hơn.
- NGUYÊN TẮC ĐỘC LẬP XÉT XỬ: đề nghị VKS chỉ là tham khảo.

BƯỚC 0: XÁC NHẬN CẤU THÀNH TỘI PHẠM (BẮT BUỘC TRƯỚC KHI ĐỊNH TỘI)
  0a. KHÁCH THỂ: Quan hệ xã hội nào bị xâm hại? (quyền sở hữu, tính mạng, sức khỏe, danh dự...)
  0b. HÀNH VI: Hành vi phạm tội cụ thể (hành động hay không hành động)? Có mối quan hệ nhân quả giữa hành vi và hậu quả?
  0c. LỖI: Cố ý trực tiếp, cố ý gián tiếp, vô ý quá tự tin, hay vô ý do cẩu thả? Hay sự kiện bất ngờ (Điều 11 BLHS 1999 / Điều 20 BLHS 2015 — loại trừ TNHS)?
  0d. CHỦ THỂ: Bị cáo có đủ năng lực TNHS (không mắc bệnh tâm thần — Điều 13 BLHS 1999 / Điều 21 BLHS 2015) và đủ tuổi chịu TNHS không?
  → Nếu BẤT KỲ yếu tố nào thiếu: ghi rõ "Cần xác minh thêm [yếu tố đó]".
  0e. LOẠI TRỪ TNHS: Có dấu hiệu phòng vệ chính đáng (Điều 15 BLHS 1999 / Điều 22 BLHS 2015), tình thế cấp thiết (Điều 16 BLHS 1999 / Điều 23 BLHS 2015), không có năng lực TNHS (Điều 13/21), hay sự kiện bất ngờ (Điều 11 BLHS 1999 / Điều 20 BLHS 2015) không? Nếu có → phân tích ngưỡng hợp pháp và xác định vượt quá nếu có.
  0f. GIAI ĐOẠN PHẠM TỘI: Tội đã hoàn thành hay chưa đạt (Điều 18 BLHS 1999 / Điều 15 BLHS 2015)? Hay mới ở giai đoạn chuẩn bị (Điều 17 BLHS 1999 / Điều 14 BLHS 2015)? Hay tự ý chấm dứt (Điều 19 BLHS 1999 / Điều 16 BLHS 2015)? → Tội chưa đạt ảnh hưởng đến hình phạt (Điều 18 BLHS 1999 / Điều 57 BLHS 2015).
  0g. ĐỒNG PHẠM (nếu nhiều bị cáo): Xác định vai trò từng người — thực hành, chủ mưu, xúi giục, giúp sức (Điều 20 BLHS 1999 / Điều 17 BLHS 2015) → ảnh hưởng đến mức hình phạt cá nhân.
BƯỚC 1: KIỂM TRA ÁN BẰNG THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 2: KIỂM TRA ĐỘ TUỔI (sử dụng số liệu đã tính ở trên nếu có).
  → Nếu bị cáo DƯỚI 18 TUỔI lúc phạm tội: BẮT BUỘC áp dụng Chương XII BLHS — mức hình phạt tối đa giảm ½ đến ¾ so với khung người thành niên, KHÔNG áp dụng tù chung thân/tử hình, ưu tiên biện pháp giáo dục tại cộng đồng.
BƯỚC 3: ĐỊNH TỘI DANH.
  3a. PHÂN TÍCH TIỀN ÁN / TÁI PHẠM (BẮT BUỘC nếu hồ sơ có tiền án): Phân biệt "tái phạm" (Điều 53 Khoản 1) và "tái phạm nguy hiểm" (Điều 53 Khoản 2) theo chuỗi thời gian:
      - Bước 0 — KIỂM TRA "TIÊU HAO TIỀN ÁN" (ƯU TIÊN TRƯỚC NHẤT, KHÔNG CÓ NGOẠI LỆ): Xét giá trị tài sản / hậu quả của vụ án HIỆN TẠI.
          ★ Nếu giá trị đó DƯỚI NGƯỠNG cấu thành tội phạm cơ bản (ví dụ: trộm cắp < 2.000.000đ), thì hành vi CHỈ CẤU THÀNH TỘI PHẠM nhờ tiền án "đã bị kết án về tội này, chưa được xóa án tích mà còn vi phạm". Khi đó, tiền án đó đã bị "TIÊU HAO" để định tội ở Khoản 1. NGUYÊN TẮC KHÔNG ÁP DỤNG KÉP (Khoản 2 Điều 52 BLHS): tiền án đã bị tiêu hao để định tội KHÔNG THỂ được dùng lại để nâng khung lên Khoản 2, DÙ tiền án đó có đủ điều kiện "tái phạm nguy hiểm" hay không. KHÔNG CÓ CON ĐƯỜNG NÀO ĐI THẲNG TỪ GIÁ TRỊ DƯỚI NGƯỠNG LÊN KHOẢN 2. → Kết luận: Khoản 1. BỎ QUA các Bước i–v bên dưới.
          ★ Ví dụ minh họa (VỤ ÁN ĐIỂN HÌNH): Bị cáo A có tiền án trộm cắp 05/2022 (chưa xóa), trộm hàng rào trị giá 476.000đ. → 476.000đ < 2.000.000đ → tiền án 05/2022 bị tiêu hao ở Khoản 1 → KHÔNG áp dụng Khoản 2 dù trước đó A còn nhiều tiền án khác → Khoản 1.
      - Bước 0 CHỈ KHÔNG áp dụng khi hành vi TỰ NÓ đã đủ cấu thành tội phạm độc lập (ví dụ: giá trị tài sản ≥ 2.000.000đ). Trong trường hợp đó, tiền án còn "tự do" và được phép kích hoạt tình tiết định khung tại Khoản 2.

      - Bước i: Liệt kê tất cả bản án trong hồ sơ theo thứ tự thời gian.
      - Bước ii: Với BẢN ÁN CHƯA ĐƯỢC XÓA ÁN TÍCH được dùng để truy tố, xác định tội phạm đó là loại gì (ít nghiêm trọng ≤3 năm / nghiêm trọng ≤7 năm / rất nghiêm trọng ≤15 năm / đặc biệt nghiêm trọng >15 năm).
      - Bước iii: TÁI PHẠM NGUY HIỂM (Khoản 2 Điều 53) CHỈ áp dụng khi ĐÁP ỨNG MỘT TRONG HAI điều kiện:
          (A) Bản án chưa xóa án tích là tội RẤT NGHIÊM TRỌNG hoặc ĐẶC BIỆT NGHIÊM TRỌNG do CỐ Ý, VÀ hành vi mới cũng là tội rất/đặc biệt nghiêm trọng do cố ý; HOẶC
          (B) Bản án chưa xóa án tích BẢN THÂN NÓ đã được xét xử trong tình trạng tái phạm (tức là khi phạm tội dẫn đến bản án đó, bị cáo đã có án tích chưa xóa trước đó).
      - Bước iv: Nếu KHÔNG thỏa mãn (A) hoặc (B) → đây CHỈ LÀ "tái phạm" đơn thuần → áp dụng tình tiết định tội tại Khoản 1 (điểm b) Điều tương ứng, KHÔNG áp dụng Khoản 2 điểm g.
      - Bước v: NGUYÊN TẮC KHÔNG ÁP DỤNG KÉP: Tình tiết đã dùng để định khung (ví dụ: tái phạm nguy hiểm tại Khoản 2 điểm g) KHÔNG được dùng lại làm tình tiết tăng nặng chung (Điều 52 Khoản 2). Ghi rõ điều này khi phân tích tình tiết.
BƯỚC 4: LƯỢNG HÌNH CHO TỪNG TỘI.
  → Phân biệt tình tiết định khung (trong khoản) và tình tiết tăng nặng chung (Điều 48 BLHS 1999 / Điều 52 BLHS 2015). KHÔNG tính trùng.
BƯỚC 4.5: KIỂM TRA DƯỚI KHUNG (Điều 47 BLHS 1999 / Điều 54 BLHS 2015): Nếu có ≥ 2 tình tiết giảm nhẹ (Điều 46/51) VÀ không có tình tiết tăng nặng (Điều 48/52) → xem xét quyết định dưới mức thấp nhất của khung. Trường hợp đặc biệt → xem xét miễn hình phạt (Điều 25 BLHS 1999 / Điều 59 BLHS 2015).
BƯỚC 5: TỔNG HỢP HÌNH PHẠT (Điều 50 BLHS 1999 / Điều 55 BLHS 2015).
BƯỚC 5.5: TỔNG HỢP VỚI BẢN ÁN CŨ (xem điều luật theo ấn bản — nếu bị cáo đang chấp hành bản án trước chưa xong).
BƯỚC 6: QUYẾT ĐỊNH HÌNH THỨC CHẤP HÀNH:
  → ÁN TREO (Điều 60 BLHS 1999 / Điều 65 BLHS 2015): CHỈ khi ĐẦY ĐỦ TẤT CẢ 5 điều kiện: (1) tổng án ≤ 3 năm, (2) nhân thân tốt, (3) có nơi cư trú và công việc ổn định, (4) không tái phạm nguy hiểm, (5) xét tính chất mức độ → không cần cách ly.
  → CẢI TẠO KHÔNG GIAM GIỮ: Xem xét nếu mức án ≤ 3 năm tù (tra số điều theo ấn bản áp dụng).
  → HÌNH PHẠT BỔ SUNG: Xem xét cấm đảm nhiệm chức vụ, phạt tiền bổ sung, tịch thu tài sản, cấm cư trú, quản chế nếu phù hợp.

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. NHẬN ĐỊNH CỦA TÒA ÁN:**
1. **Phân tích cấu thành tội phạm:** (xem xét đủ 4 yếu tố cấu thành — khách thể, mặt khách quan, mặt chủ quan, chủ thể)
2. **Định tội danh:** (liệt kê từng hành vi + điều khoản + khung hình phạt)
3. **Phân tích tình tiết:**
   - Tình tiết Tăng nặng (Điều 48 BLHS 1999 / Điều 52 BLHS 2015):
   - Tình tiết Giảm nhẹ (Điều 46 BLHS 1999 / Điều 51 BLHS 2015):
4. **Nhân thân:**

**II. QUYẾT ĐỊNH:**
1. Tuyên bố bị cáo phạm tội...
2. Áp dụng điều khoản...
3. HÌNH PHẠT: (tù giam HOẶC phạt tiền, chọn 1)
4. TRÁCH NHIỆM DÂN SỰ & XỬ LÝ VẬT CHỨNG
5. ÁN PHÍ: 200.000 đồng

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Nguồn áp dụng | Lý do chọn nguồn |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
"""

        prompt = ChatPromptTemplate.from_template(prompt_template)

        # Change history messages into List[BaseMessage]
        history_msgs = []
        if history:
            for msg in history[-8:]:
                if msg.get("role") == "user":
                    history_msgs.append(HumanMessage(content=sanitize_text(msg.get("content", ""))))
                else:
                    history_msgs.append(AIMessage(content=sanitize_text(msg.get("content", ""))))

        chain = prompt | llm | StrOutputParser()

        try:
            formatted_prompt = prompt.format_messages(
                role_instruction=role_instruction,
                context=context_text,
                case_details=case_details,
                deterministic_context=det_context,
                mapped_context=mapped_context,
                nhan_than_context=nhan_than_context,
            )
            final_messages = history_msgs + formatted_prompt
            response = llm.invoke(_sanitize_msgs(final_messages)).content
        except Exception as e:
            return {"messages": [AIMessage(content=f"Lỗi xử lý: {e}")]}

        cleaned_response = cleanup_response(response)
        return {"messages": [AIMessage(content=cleaned_response)]}

    # ─────────────────────────────────────────────────────────────────────
    # NODE: ANSWER VERIFY
    # ─────────────────────────────────────────────────────────────────────
    @measure_time('answer_verify')
    def answer_verify_node(state: AgentState) -> dict:
        """
        Final answer verification — 2-layer hybrid, cost-optimised.

        Layer 1: Pure Python ($0, always runs)
          L1-A  Hallucination  — article numbers cited but not in retrieved context
          L1-B  Temporal       — wrong BLHS edition cited for the crime date
          L1-C  Role signal    — keyword direction mismatch for assigned role

        Layer 2: LLM Judge (~$0.0005/call, skipped if L1 already found >= 2 issues)
          Q1  Factual consistency — did the AI invent facts not in extracted_facts?
          Q2  Role adherence      — is tone/argument direction consistent with role?

        On success: returns {} (silent pass-through).
        On issues:  appends a warning block to the last AIMessage.
        """
        print("[NODE: answer_verify]")
        messages = state.get("messages") or []
        last_msg = messages[-1] if messages else None
        if not last_msg or not isinstance(last_msg, AIMessage):
            print("  [VERIFY] No AIMessage found — skipping verification.")
            return {}

        ai_text     = last_msg.content
        role        = state.get("user_role", "neutral")
        facts       = state.get("extracted_facts") or {}
        mapped_laws = state.get("mapped_laws") or []
        documents   = state.get("documents") or []
        crime_date  = facts.get("ngay_pham_toi", "")

        cited_articles = set(_ARTICLE_CITE_PAT.findall(ai_text))
        print(f"  [VERIFY] role={role} | crime_date={crime_date} | "
              f"mapped_laws={len(mapped_laws)} | docs={len(documents)} | "
              f"cited_articles={sorted(cited_articles)}")

        issues: List[str] = []

        # L1-A: Hallucination check
        hallucinated = _verify_no_hallucinated_articles(ai_text, mapped_laws, documents)
        if hallucinated:
            arts = ", ".join(f"Điều {a}" for a in hallucinated)
            issues.append(
                f"Trích dẫn không có cơ sở: {arts} "
                f"— không tìm thấy trong văn bản luật đã truy xuất."
            )
            print(f"  [VERIFY L1-A] ❌ Hallucinated articles: {hallucinated}")
        else:
            print("  [VERIFY L1-A] ✅ All cited articles verified.")

        # L1-B: Temporal validity check
        per_defendant = state.get("per_defendant_dates") or None
        wrong_edition = _verify_temporal_validity(
            ai_text, crime_date, documents, per_defendant_dates=per_defendant
        )
        if wrong_edition:
            correct = _edition_for_date(crime_date) or "không xác định"
            issues.append(
                f"Có thể áp dụng sai phiên bản luật: {', '.join(wrong_edition)}. "
                f"Ngày phạm tội {crime_date} → phải dùng {correct}."
            )
            print(f"  [VERIFY L1-B] ❌ Wrong edition(s) cited: {wrong_edition} "
                  f"(expected: {correct})")
        else:
            print("  [VERIFY L1-B] ✅ Temporal edition check passed.")

        # L1-C: Role signal check
        role_score = _verify_role_signal(ai_text, role)
        if role_score < 0.35:
            issues.append(
                f"Giọng văn có thể chưa nhất quán với vai '{role}' "
                f"(điểm tín hiệu = {role_score:.2f}/1.00)."
            )
            print(f"  [VERIFY L1-C] ❌ Role signal weak: score={role_score:.2f} "
                  f"(threshold=0.35) for role='{role}'")
        else:
            print(f"  [VERIFY L1-C] ✅ Role signal OK: score={role_score:.2f} "
                  f"for role='{role}'")

        # Layer 2 — LLM Judge (skip if L1 already caught >= 2 issues)
        if len(issues) < 2:
            response_snippet = ai_text[:1500]
            key_facts = {
                k: facts.get(k)
                for k in [
                    "hanh_vi", "hau_qua", "co_tien_an",
                    "tinh_tiet_tang_nang", "tinh_tiet_giam_nhe", "ngay_pham_toi",
                ]
                if facts.get(k) is not None
            }
            fact_lines = []
            if key_facts.get("hanh_vi"):
                fact_lines.append(f"- Hành vi phạm tội: {key_facts['hanh_vi']}")
            if key_facts.get("hau_qua"):
                fact_lines.append(f"- Hậu quả: {key_facts['hau_qua']}")
            if key_facts.get("ngay_pham_toi"):
                fact_lines.append(f"- Ngày phạm tội: {key_facts['ngay_pham_toi']}")
            if key_facts.get("co_tien_an") is not None:
                if key_facts["co_tien_an"]:
                    tien_an_text = "CÓ TIỀN ÁN (bản án còn hiệu lực — có thể là tình tiết tái phạm)"
                else:
                    tien_an_text = ("không có tiền án chính thức (Tiền án: Không trong hồ sơ, "
                                    "tuy nhiên nếu hồ sơ có mục Nhân thân liệt kê bản án cũ đã xóa án tích, "
                                    "AI được phép đề cập đây là 'nhân thân xấu' — đây là phân tích ĐÚNG, "
                                    "KHÔNG phải mâu thuẫn với co_tien_an=false)")
                fact_lines.append(f"- Nhân thân bị cáo: {tien_an_text}")
            if key_facts.get("tinh_tiet_tang_nang"):
                fact_lines.append(f"- Tình tiết tăng nặng: {key_facts['tinh_tiet_tang_nang']}")
            if key_facts.get("tinh_tiet_giam_nhe"):
                fact_lines.append(f"- Tình tiết giảm nhẹ: {key_facts['tinh_tiet_giam_nhe']}")
            fact_summary = "\n".join(fact_lines) if fact_lines else "(Không có dữ liệu)"

            role_map = {
                "defense": "Luật sư bào chữa — phải bảo vệ bị cáo, xin giảm nhẹ.",
                "victim":  "Luật sư bị hại — phải đòi xử nghiêm, bồi thường tối đa.",
                "neutral": "Thẩm phán — phải trung lập, phân tích hai chiều.",
            }
            raw_case_text = state.get("full_case_content", state["question"])

            judge_prompt = (
                "Bạn là kiểm tra viên pháp lý. Đánh giá đoạn phân tích AI dưới đây.\n\n"
                f"SỰ KIỆN THỰC TẾ (Bản tóm tắt):\n"
                f"{fact_summary}\n\n"
                f"NGUYÊN VĂN VỤ ÁN TỪ NGƯỜI DÙNG (Căn cứ gốc):\n"
                f"{raw_case_text}\n\n"
                f"VAI TRÒ YÊU CẦU: {role_map.get(role, role)}\n\n"
                f"ĐOẠN PHÂN TÍCH AI (đã rút gọn):\n{response_snippet}\n\n"
                'Trả lời 2 câu hỏi sau bằng JSON:\n'
                '{\n'
                '  "factual_ok": true/false,\n'
                '  "factual_issue": "TỐI ĐA 15 TỪ tiếng Việt nếu false, null nếu true",\n'
                '  "role_ok": true/false,\n'
                '  "role_issue": "TỐI ĐA 15 TỪ tiếng Việt nếu false, null nếu true"\n'
                '}\n\n'
                "QUY TẮC:\n"
                "- factual_ok = false CHỈ KHI AI bịa ra chi tiết cụ thể (số tiền, số bản án, ngày tháng cụ thể) KHÔNG có trong NGUYÊN VĂN VỤ ÁN.\n"
                "- Nếu thông tin có trong NGUYÊN VĂN VỤ ÁN nhưng không có trong Bản tóm tắt thì VẪN HỢP LỆ (factual_ok = true).\n"
                "- role_ok = false CHỈ KHI AI rõ ràng lập luận SAI chiều với vai trò được giao.\n"
                "- Nếu không chắc → true (tránh false positive).\n"
                "- TUYỆT ĐỐI KHÔNG đề cập tên trường kỹ thuật trong mô tả.\n"
                "- Viết mô tả NGẮN GỌN tối đa 15 từ, dễ hiểu cho người dùng thông thường.\n"
                "OUTPUT: Chỉ JSON hợp lệ, không markdown."
            )
            try:
                judge_llm = llm.bind(max_tokens=400)
                judge_resp = judge_llm.invoke(
                    _sanitize_msgs([HumanMessage(content=judge_prompt)])
                )
                verdict = _extract_json(judge_resp.content)
                if not verdict.get("factual_ok", True) and verdict.get("factual_issue"):
                    issues.append(f"Nhất quán dữ liệu thực tế: {verdict['factual_issue']}")
                if not verdict.get("role_ok", True) and verdict.get("role_issue"):
                    issues.append(f"Vai trò: {verdict['role_issue']}")
            except Exception as judge_err:
                print(
                    f"  [answer_verify] L2 judge error "
                    f"({type(judge_err).__name__}): {judge_err} — skipping L2."
                )
        else:
            print(
                f"  [answer_verify] L2 skipped — "
                f"L1 already found {len(issues)} issue(s)."
            )

        if not issues:
            print("  ✅ Answer verification passed — no issues detected.")
            return {}

        warning_block = (
            "\n\n---\n"
            "**Ghi chú hệ thống (Kiểm chứng câu trả lời):**\n"
            + "\n".join(f"- {i}" for i in issues)
            + "\n\n*Vui lòng đối chiếu với văn bản luật gốc để xác nhận.*"
        )
        print(f"  ⚠️ Answer verification: {len(issues)} issue(s) detected.")
        return {"messages": [AIMessage(content=ai_text + warning_block)]}

    # ─────────────────────────────────────────────────────────────────────
    # NODE: PRACTICE EVALUATE
    # ─────────────────────────────────────────────────────────────────────
    @measure_time('practice_evaluate')
    def practice_evaluate_node(state: AgentState) -> dict:
        """
        Practice Mode final node.
        Grades the user's submitted legal analysis against ground-truth retrieved laws.
        Returns a JSON-encoded message that /practice/evaluate parses into PracticeEvalResponse.
        """
        print("[NODE: practice_evaluate]")
        role          = state.get("user_role", "neutral")
        mapped_laws   = state.get("mapped_laws") or []
        documents     = state.get("documents") or []
        user_analysis = (state.get("user_analysis") or "").strip()

        if not user_analysis:
            fallback = json.dumps({
                "score": 0,
                "feedback": {
                    "strengths": [],
                    "improvements": ["Bạn chưa cung cấp nội dung phân tích. Vui lòng viết phân tích pháp lý của bạn và thử lại."],
                    "suggestion": "Hãy viết phân tích pháp lý theo vai trò đã chọn trước khi gửi để chấm điểm.",
                    "suggested_laws": [],
                },
            }, ensure_ascii=False)
            return {"messages": [AIMessage(content=fallback)]}

        primary_docs    = [d for d in documents if d.metadata.get("_temporal_role") == "primary"]
        comparison_docs = [d for d in documents if d.metadata.get("_temporal_role") == "comparison"]
        adjustment_docs = [d for d in documents if d.metadata.get("_temporal_role") == "adjustment"]
        ordered_docs    = primary_docs + comparison_docs + adjustment_docs

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"vai_trò={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in ordered_docs
        ]))

        role_label = {
            "neutral": "Thẩm phán (trung lập)",
            "defense": "Luật sư bào chữa (bảo vệ bị cáo)",
            "victim":  "Luật sư bảo vệ bị hại",
        }.get(role, "Chuyên gia pháp lý")

        role_criteria = {
            "neutral": """
- Xác nhận đủ 4 yếu tố cấu thành tội phạm trước khi định tội.
- Kiểm tra loại trừ TNHS (phòng vệ chính đáng Điều 15/22, tình thế cấp thiết Điều 16/23, không có năng lực TNHS Điều 13/21, sự kiện bất ngờ Điều 11/20).
- Xác định đúng giai đoạn phạm tội và áp dụng hình phạt tương ứng (Điều 18 BLHS 1999 / Điều 57 BLHS 2015).
- Xác định vai trò đồng phạm (Điều 20 BLHS 1999 / Điều 17 BLHS 2015) nếu nhiều bị cáo.
- Xác định đúng tội danh và điều luật áp dụng (đúng phiên bản BLHS theo ngày phạm tội).
- Phân biệt tình tiết định khung (trong khoản) và tình tiết tăng nặng chung (Điều 48/52) — KHÔNG tính trùng.
- Phân tích đầy đủ cả tình tiết giảm nhẹ (Điều 46/51) VÀ tăng nặng (Điều 48/52) một cách trung lập.
- Lượng hình hợp lý trong đúng khung, có tổng hợp hình phạt (Điều 50/55) nếu nhiều tội.
- Kiểm tra dưới khung (Điều 47/54) nếu có ≥ 2 tình tiết giảm nhẹ và không tăng nặng.
- Tính thời gian tạm giam đã khấu trừ vào mức án.
- Áp dụng đúng quy tắc người dưới 18 tuổi (Chương XII) nếu bị cáo chưa thành niên.
- Xem xét án treo (Điều 60/65 — đủ 5 điều kiện) hoặc cải tạo không giam giữ.
- Quyết định về trách nhiệm dân sự (vật chất + tinh thần) và hình phạt bổ sung.
- Tính khách quan, không thiên lệch về phía bị cáo hay bị hại.
""",
            "defense": """
- Kiểm tra cấu thành tội phạm — nếu thiếu yếu tố nào → lập luận hành vi không cấu thành tội hoặc cấu thành tội nhẹ hơn.
- Kiểm tra loại trừ TNHS (phòng vệ chính đáng Điều 15/22, tình thế cấp thiết Điều 16/23) nếu có dấu hiệu.
- Xác định giai đoạn phạm tội — nếu chưa đạt/chuẩn bị → viện dẫn điều luật tương ứng (Điều 17-19 BLHS 1999 / Điều 14-16 BLHS 2015).
- Nếu đồng phạm vai trò phụ (giúp sức) → nhấn mạnh mức độ đóng góp thấp (Điều 20/17).
- Phát hiện và chứng dẫn đầy đủ tình tiết giảm nhẹ theo Điều 46 (BLHS 1999) / Điều 51 (BLHS 2015).
- Viện dẫn dưới khung (Điều 47/54) nếu có ≥ 2 tình tiết giảm nhẹ và không tăng nặng.
- Xem xét miễn hình phạt (Điều 25 BLHS 1999 / Điều 59 BLHS 2015) trong trường hợp đặc biệt.
- Đề xuất áp dụng án treo (Điều 60/65 — đủ 5 điều kiện) hoặc cải tạo không giam giữ.
- Phản bác hiệu quả các tình tiết tăng nặng do bên buộc tội đưa ra.
- Đề nghị khung hình phạt nhẹ nhất có thể, có căn cứ pháp lý rõ ràng.
- Yêu cầu khấu trừ thời gian tạm giam theo quy định.
- Áp dụng quy tắc người dưới 18 tuổi (Chương XII) nếu thân chủ chưa thành niên.
""",
            "victim": """
- Khẳng định đủ 4 yếu tố cấu thành tội phạm — bác bỏ mọi lập luận thiếu yếu tố từ phía bị cáo.
- Phản bác loại trừ TNHS (phòng vệ chính đáng, tình thế cấp thiết) nếu bị cáo viện dẫn → chứng minh vượt quá.
- Khẳng định tội đã hoàn thành (phản bác tội chưa đạt nếu bị cáo lập luận).
- Xác định vai trò chủ mưu/chính phạm nếu nhiều bị cáo → yêu cầu xử nặng (Điều 20/17).
- Xác định và nhấn mạnh đầy đủ các tình tiết tăng nặng theo Điều 48 (BLHS 1999) / Điều 52 (BLHS 2015).
- Phân biệt tình tiết định khung và tình tiết tăng nặng chung — không bỏ sót.
- Yêu cầu mức hình phạt cao nhất trong khung, có căn cứ từ hậu quả thực tế.
- Tính toán và yêu cầu bồi thường dân sự đầy đủ (vật chất + tinh thần + phát sinh).
- Phản bác các tình tiết giảm nhẹ không có cơ sở hoặc không đáng kể.
- Phản bác án treo — chỉ ra điều kiện nào của Điều 60/65 không thỏa mãn.
- Yêu cầu hình phạt bổ sung (tịch thu, cấm chức vụ, phạt tiền bổ sung).
- Bảo vệ toàn diện quyền và lợi ích hợp pháp của bị hại.
""",
        }.get(role, "")

        mapped_laws_text = json.dumps(mapped_laws, ensure_ascii=False, indent=2) if mapped_laws else "(Không có dữ liệu ánh xạ điều luật)"

        eval_prompt = f"""Bạn là giáo sư luật hình sự Việt Nam có kinh nghiệm đang chấm bài phân tích pháp lý của người học.

VAI TRÒ CỦA NGƯỜI HỌC: {role_label}

VĂN BẢN LUẬT ĐÃ TRA CỨU (từ hệ thống RAG — đây là chuẩn để đối chiếu):
{context_text}

KẾT QUẢ ÁNH XẠ ĐIỀU LUẬT CHUẨN (hệ thống xác định):
{mapped_laws_text}

BÀI PHÂN TÍCH CỦA NGƯỜI HỌC:
{user_analysis}

TIÊU CHÍ CHẤM ĐIỂM (dành cho vai trò {role_label}):
{role_criteria}

NHIỆM VỤ:
1. Đánh giá tính chính xác của các điều luật, khoản mục, và phiên bản Bộ luật Hình sự mà người học viện dẫn.
2. Đánh giá chất lượng, tính logic và sức thuyết phục của lập luận dựa trên tiêu chí vai trò đã nêu.
3. Chỉ ra các điểm mạnh và những thiếu sót cần cải thiện trong bài phân tích.
4. Chấm điểm tổng thể từ 0 đến 100.

QUY TẮC CHẤM ĐIỂM:
- 90–100: Phân tích xuất sắc, chính xác hoàn toàn, lập luận sắc bén và đầy đủ.
- 70–89: Phân tích tốt, nắm được các điểm chính, có thể thiếu 1–2 chi tiết phụ.
- 50–69: Phân tích trung bình, có một số sai sót về điều luật hoặc thiếu luận điểm quan trọng.
- 30–49: Phân tích yếu, sai nhiều điều luật hoặc bỏ sót nhiều điểm mấu chốt.
- 0–29: Phân tích rất yếu hoặc không có nội dung pháp lý thực chất.

Trả về JSON hợp lệ (không markdown, không giải thích bên ngoài JSON):
{{
  "score": <số nguyên từ 0 đến 100>,
  "feedback": {{
    "strengths": ["<điểm mạnh cụ thể 1>", "<điểm mạnh cụ thể 2>", ...],
    "improvements": ["<điểm cần cải thiện cụ thể 1 kèm tham chiếu điều luật>", ...],
    "suggestion": "<1–2 câu gợi ý cụ thể và thiết thực nhất cho người học>"
  }}
}}

Quy tắc:
- strengths: 2–4 điểm, phải trích dẫn cụ thể luận điểm của người học.
- improvements: 2–5 điểm, phải nêu điều khoản cụ thể cần bổ sung hoặc sửa đổi.
- suggestion: tập trung vào hành động cụ thể người học cần làm tiếp theo.
OUTPUT: CHỈ JSON hợp lệ."""

        try:
            raw_response = llm.invoke(_sanitize_msgs([
                HumanMessage(content=eval_prompt)
            ])).content
            data = _extract_json(raw_response)

            feedback = data.get("feedback", {})
            raw_score = data.get("score", 50)
            try:
                score = max(0, min(100, int(raw_score)))
            except (TypeError, ValueError):
                score = 50

            seen: set = set()
            suggested_laws: list = []
            for d in (primary_docs + comparison_docs):
                temporal_role = d.metadata.get("_temporal_role", "")
                is_pinned     = d.metadata.get("_pinned", False)
                if temporal_role == "adjustment" or is_pinned:
                    continue

                art    = str(d.metadata.get("article_number", "")).strip()
                clause = str(d.metadata.get("clause", "")).strip()
                name   = str(d.metadata.get("title", "")).strip()
                source = str(d.metadata.get("source", "")).strip()

                if not art or art in ("N/A", ""):
                    continue
                if art.lower().startswith("điều "):
                    art = art[5:].strip()

                source = source.strip()
                key = (art, source)
                if key not in seen:
                    seen.add(key)
                    suggested_laws.append({
                        "article":      art,
                        "clause":       clause,
                        "offense_name": name,
                        "source":       source,
                    })

            result_payload = json.dumps({
                "score": score,
                "feedback": {
                    "strengths":      feedback.get("strengths", []),
                    "improvements":   feedback.get("improvements", []),
                    "suggestion":     feedback.get("suggestion", ""),
                    "suggested_laws": suggested_laws,
                },
            }, ensure_ascii=False)
            return {"messages": [AIMessage(content=result_payload)]}

        except Exception as e:
            print(f"  [PRACTICE EVAL ERROR] {type(e).__name__}: {e}")
            fallback = json.dumps({
                "score": 0,
                "feedback": {
                    "strengths": [],
                    "improvements": [f"Lỗi hệ thống khi chấm điểm: {type(e).__name__}: {e}"],
                    "suggestion": "Vui lòng thử lại. Nếu lỗi tiếp tục, hãy kiểm tra kết nối và nội dung phân tích.",
                    "suggested_laws": [],
                },
            }, ensure_ascii=False)
            return {"messages": [AIMessage(content=fallback)]}

    # ─────────────────────────────────────────────────────────────────────
    # NODE: CASUAL RESPOND
    # ─────────────────────────────────────────────────────────────────────
    @measure_time('casual_respond')
    def casual_respond_node(state: AgentState) -> dict:
        """Handle greetings, off-topic, and unrelated queries."""
        print("[NODE: casual_respond]")
        question = state["question"].strip().lower()

        GREETINGS = ["hi", "hello", "chào", "xin chào", "hey", "helo", "ola"]
        is_greeting = any(q in question for q in GREETINGS) or len(question) <= 10
        print(f"  [CASUAL] is_greeting={is_greeting} | query='{state['question'][:60]}'")

        if is_greeting:
            reply = (
                "Xin chào! Tôi là **Trợ lý Pháp luật Hình sự VNPLaw**\n\n"
                "Tôi được xây dựng chuyên biệt để hỗ trợ **phân tích và tra cứu pháp luật hình sự Việt Nam**. "
                "Dưới đây là những gì tôi có thể làm cho bạn:\n\n"
                " **Phân tích vụ án hình sự**\n"
                "   Định tội danh, xác định khung hình phạt, lượng hình cụ thể theo Bộ luật Hình sự\n\n"
                " **Phân tích đa chiều theo vai trò**\n"
                "   • *Thẩm phán* - nhận định trung lập, khách quan\n"
                "   • *Luật sư bào chữa* - lập luận giảm nhẹ, bảo vệ bị cáo\n"
                "   • *Luật sư bị hại* - yêu cầu xử nghiêm, bồi thường tối đa\n\n"
                " **Tra cứu & giải thích điều luật**\n"
                "   Trích dẫn chính xác điều khoản BỘ luật hình sự, giải thích tình tiết tăng nặng/giảm nhẹ\n\n"
                "---\n"
                " **Hướng dẫn:** Dán toàn bộ nội dung hồ sơ vụ án và tôi sẽ bắt đầu phân tích.\n"
                "*Ví dụ: \"Ngày 15/3/2023, Nguyễn Văn A dùng dao đe dọa lấy tài sản của bị hại...\"*"
            )
        else:
            reply = (
                "Xin lỗi, lĩnh vực này nằm ngoài phạm vi hỗ trợ của tôi. \n\n"
                "Tôi chuyên về **pháp luật hình sự Việt Nam** — nếu bạn có:\n"
                "- Hồ sơ vụ án cần phân tích tội danh và hình phạt\n"
                "- Câu hỏi về điều khoản BLHS, tình tiết tăng nặng/giảm nhẹ\n"
                "- Cần lập luận theo góc độ thẩm phán, luật sư bào chữa hoặc luật sư bị hại\n\n"
                "Hãy chia sẻ và tôi sẽ hỗ trợ ngay! "
            )

        return {"messages": [AIMessage(content=reply)]}

    # ─────────────────────────────────────────────────────────────────────
    # NODE: FOLLOW-UP GENERATE
    # ─────────────────────────────────────────────────────────────────────
    @measure_time('followup_generate')
    def followup_generate_node(state: AgentState) -> dict:
        """
        Handle follow-up questions about a prior response.

        With MemorySaver checkpointing, documents/mapped_laws/extracted_facts
        are restored from Turn 1. If no checkpoint exists, falls back to
        fresh retrieval from the original case message.
        """
        print("[NODE: followup_generate]")
        mapped_laws  = state.get("mapped_laws") or []
        question     = state["question"]
        role         = state.get("user_role", "neutral")
        full_history = state.get("chat_history") or []
        chat_history = full_history[-8:]

        print(f"  [FOLLOWUP] role={role} | history_turns={len(chat_history)} | query='{question[:60]}'")
        print(f"  [FOLLOWUP] restored mapped_laws={len(mapped_laws)} entries")

        # Find original case message from history
        original_case = next(
            (m["content"] for m in full_history
             if m.get("role") == "user" and _looks_like_case(m.get("content", ""))),
            question,
        )
        print(f"  [FOLLOWUP] original_case source: '{original_case[:60]}...' ({len(original_case)} chars)")

        # Documents: checkpoint or fallback retrieval
        documents = state.get("documents") or []
        if documents:
            print(f"  [FOLLOWUP] {len(documents)} docs restored from checkpoint (skipping retrieval)")
        else:
            print("  [FOLLOWUP] no docs in state — running fallback retrieval")
            try:
                documents = retriever.invoke(original_case[:10000], top_k_override=10)
                print(f"  [FOLLOWUP] semantic: {len(documents)} docs from original case")
            except Exception as e:
                print(f"  [FOLLOWUP] Semantic retrieval failed: {e}")
                documents = []

            # BM25 on the raw follow-up question
            if bm25_index is not None and bm25_docs:
                try:
                    seen_ids = {
                        (d.metadata.get("article_number", ""), d.metadata.get("source", ""))
                        for d in documents
                    }
                    tokenized_q = question.lower().split()
                    scores      = bm25_index.get_scores(tokenized_q)
                    top_indices = sorted(
                        range(len(scores)), key=lambda i: scores[i], reverse=True
                    )[:5]
                    added_bm25 = 0
                    for idx in top_indices:
                        if scores[idx] <= 0:
                            break
                        src = bm25_docs[idx]
                        key = (src.metadata.get("article_number", ""), src.metadata.get("source", ""))
                        if key not in seen_ids:
                            seen_ids.add(key)
                            documents.append(Document(
                                page_content=src.page_content,
                                metadata={**src.metadata, "_retrieval_source": "bm25_followup"},
                            ))
                            added_bm25 += 1
                    print(f"  [FOLLOWUP] bm25: {added_bm25} new unique docs added")
                except Exception as e:
                    print(f"  [FOLLOWUP] BM25 retrieval failed: {e}")

            # Temporal role tagging (fallback only)
            primary_edition = None
            if mapped_laws:
                first_law = mapped_laws[0]
                if isinstance(first_law, dict):
                    edition_val = first_law.get("edition_applied")
                    if edition_val and edition_val != "N/A":
                        primary_edition = edition_val

            if primary_edition:
                print(f"  [FOLLOWUP] tagging fallback docs with primary_edition='{primary_edition}'")
                tagged_docs = []
                for d in documents:
                    meta = dict(d.metadata)
                    if not meta.get("_temporal_role"):
                        if meta.get("source") == primary_edition:
                            meta["_temporal_role"] = "primary"
                        else:
                            meta["_temporal_role"] = "comparison"
                    tagged_docs.append(Document(page_content=d.page_content, metadata=meta))
                documents = tagged_docs

        print(f"  [FOLLOWUP] total docs for LLM: {len(documents)}")

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ]))
        history_messages = [
            HumanMessage(content=sanitize_text(m.get("content", "")))
            if m.get("role") == "user"
            else AIMessage(content=sanitize_text(m.get("content", "")))
            for m in chat_history
        ]
        print("  [FOLLOWUP] Invoking LLM for follow-up response...")
        start_llm = time.time()
        response = llm.invoke(_sanitize_msgs([
            SystemMessage(content=(
                f"Bạn là chuyên gia luật hình sự Việt Nam, góc độ: {role}.\n"
                "Dựa vào văn bản luật và kết quả ánh xạ đã có, trả lời câu hỏi tiếp theo.\n"
                "Giữ nguyên quy tắc hiệu lực luật (Điều 7 Bộ luật Hình sự) từ lượt phân tích trước."
            )),
            *history_messages,
            HumanMessage(content=(
                f"CÂU HỎI: {question}\n\n"
                f"VĂN BẢN LUẬT (tham khảo):\n{context_text}\n\n"
                f"KẾT QUẢ ÁNH XẠ (nếu có):\n{json.dumps(mapped_laws, ensure_ascii=False)}"
            ))
        ]))
        elapsed_llm = time.time() - start_llm
        print(f"  [FOLLOWUP] LLM generation finished in {elapsed_llm:.2f}s")
        return {"messages": [AIMessage(content=cleanup_response(response.content))]}

    return {
        "generate":          generate_node,
        "answer_verify":     answer_verify_node,
        "practice_evaluate": practice_evaluate_node,
        "casual_respond":    casual_respond_node,
        "followup_generate": followup_generate_node,
    }
