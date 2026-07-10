"""
graph/nodes/generation.py — VNPLaw AI Service
Nodes: generate, answer_verify, practice_evaluate, casual_respond,
       followup_generate.

Routing functions (classify_intent, application_mode_router) have been
moved to routing.py. Keyword lists are re-imported here for the
_looks_like_case helper used inside make_generation_nodes.
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
from app.utils.clean_json import _extract_json
from app.utils.text import sanitize_text, _sanitize_msgs, cleanup_response

# Keyword lists are defined in routing.py; imported here for the
# _looks_like_case helper used inside make_generation_nodes.
from app.graph.nodes.routing import CASUAL_PHRASES, LEGAL_KEYWORDS, FOLLOWUP_PHRASES


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

        # ── DEBUG: show exactly what chunks go into the LLM ──────────────────
        print(f"  [GENERATE INPUT] {len(ordered_docs)} docs → LLM (role={role}):")
        for _d in ordered_docs:
            _art  = _d.metadata.get("article_number", "?")
            _src  = _d.metadata.get("source", "?")
            _rtag = _d.metadata.get("_temporal_role", "?")
            _pin  = "📌 " if _d.metadata.get("_pinned") else "   "
            _prev = _d.page_content[:100].replace("\n", " ")
            print(f"  {_pin}Điều {str(_art):>4} | {str(_src):<30} | {str(_rtag):<12} | {_prev}...")
        # ─────────────────────────────────────────────────────────────────────

        # ── Build mapped_laws context ─────────────────────────────────────────
        mapped_context = ""
        if mapped_laws and not (len(mapped_laws) == 1 and mapped_laws[0].get("_mapping_error")):
            lines = ["**Tội danh đã được xác định (từ hồ sơ vụ án):**"]
            for law in mapped_laws:
                err = law.get("_mapping_error", False)
                lines.append(
                    f"- {law.get('article','?')} {law.get('clause','?')}: "
                    f"{law.get('offense_name','?')} "
                    f"[{law.get('edition_applied','?')}]"
                    + (" ⚠️ (ánh xạ có thể không chính xác)" if err else "")
                )
                # For defense/victim roles only: expose alternative charges for advocacy.
                # For judge (neutral): only the primary offense is shown — no alternatives.
                if role != "neutral":
                    alt_l = law.get("alternative_lighter_article")
                    alt_h = law.get("alternative_heavier_article")
                    if alt_l:
                        lines.append(
                            f"  ↳ Phương án nhẹ hơn có thể tranh luận: "
                            f"{alt_l.get('article','?')} {alt_l.get('clause','?')} "
                            f"— {alt_l.get('offense_name','?')} "
                            f"(lý do: {alt_l.get('reason','?')})"
                        )
                    if alt_h:
                        lines.append(
                            f"  ↳ Phương án nặng hơn có thể tranh luận: "
                            f"{alt_h.get('article','?')} {alt_h.get('clause','?')} "
                            f"— {alt_h.get('offense_name','?')} "
                            f"(lý do: {alt_h.get('reason','?')})"
                        )
            mapped_context = "\n".join(lines)
        else:
            mapped_context = "**Lưu ý:** Không thể xác định tội danh cụ thể từ thông tin đã cung cấp."

        # ── Build explicit charge block for judge role only ───────────────────
        # This is injected at the very top of the judge prompt (highest LLM
        # attention zone) to prevent the LLM from switching to a different
        # article it may encounter inside legal_context documents.
        judge_charge_block = ""
        if role == "neutral" and mapped_laws and not (len(mapped_laws) == 1 and mapped_laws[0].get("_mapping_error")):
            first_law = mapped_laws[0]
            _art   = first_law.get("article", "?")
            _cl    = first_law.get("clause", "")
            _name  = first_law.get("offense_name", "?")
            _ed    = first_law.get("edition_applied", "?")
            judge_charge_block = (
                f"BẮT BUỘC:\n"
                f"TỘI DANH PHẢI XÉT XỬ DỰA TRÊN TỘI DANH VÀ ĐIỀU KHOẢN ĐÃ ĐƯỢC XÁC ĐỊNH SAU: {_art} {_cl} — {_name} [{_ed}]\n"
                f"TÒA ÁN CHỈ ĐƯỢC SỬ DỤNG TỘI DANH VÀ KHOẢN NÀY. TUYỆT ĐỐI KHÔNG ĐƯỢC tự chuyển sang "
                f"bất kỳ tội danh (Điều) hay khung hình phạt (Khoản) nào khác (kể cả các điều khoản xuất hiện trong `legal_context`).\n"
                f"KHÔNG VIẾT \"Phương án 1\", \"Phương án 2\", hay bất kỳ lựa chọn thay thế nào.\n"
            )

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
                "Bạn là Luật sư Bào chữa, đang bảo vệ thân chủ. "
                "TUYỆT ĐỐI KHÔNG sử dụng từ 'hệ thống' (ví dụ không viết 'hệ thống đã xác định...'). Hãy xưng hô là 'Luật sư' hoặc 'Chúng tôi'.\n"
                "Nhiệm vụ: phân tích pháp lý CHỈ THEO HƯỚNG CÓ LỢI cho thân chủ. "
                "TUYỆT ĐỐI không đề xuất mức án nặng hơn. "
                "Nếu phải đề cập tình tiết tăng nặng: CHỈ để phản bác hoặc giảm thiểu tác động."
            ),
            "victim": (
                "Bạn là Luật sư Bảo vệ Bị hại. "
                "TUYỆT ĐỐI KHÔNG sử dụng từ 'hệ thống' (ví dụ không viết 'hệ thống đã xác định...'). Hãy xưng hô là 'Luật sư' hoặc 'Chúng tôi'.\n"
                "Nhiệm vụ: phân tích pháp lý CHỈ THEO HƯỚNG BẢO VỆ QUYỀN LỢI BỊ HẠI TỐI ĐA. "
                "TUYỆT ĐỐI không đề xuất án nhẹ hơn cho bị cáo. "
                "Nếu phải đề cập tình tiết giảm nhẹ: CHỈ để phản bác hoặc chứng minh không đủ điều kiện."
            ),
            "neutral": (
                "Bạn là Thẩm phán Hội đồng xét xử, "
                "đang ra phán quyết trung lập, khách quan, hai chiều dựa trên pháp luật. "
                "TUYỆT ĐỐI KHÔNG sử dụng từ 'hệ thống' (ví dụ không viết 'hệ thống đã xác định...'). Hãy xưng hô là 'Hội đồng xét xử' hoặc 'Tòa án'."
            ),
        }
        role_instruction = role_instructions.get(role, role_instructions["neutral"])

        # ── Select prompt template based on role ──────────────────────────────
        if role == "defense":
            prompt_template = """{role_instruction}

⛔ TUYỆT ĐỐI KHÔNG viết lời chào mở đầu như "Kính thưa Hội đồng xét xử", "Với kinh nghiệm X năm", hay bất kỳ phần giới thiệu bản thân nào. Bắt đầu NGAY VÀO NỘI DUNG phân tích pháp lý.

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy lập luận BẢO VỆ thân chủ.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}

⚠️ TỘI DANH HỆ THỐNG XÁC ĐỊNH (căn cứ trung lập):
{mapped_context}

HƯỚNG DẪN SỬ DỤNG TỘI DANH TRONG VAI TRÒ BÀO CHỮA (BẮT BUỘC TUÂN THỦ):
● Nếu phương án nhẹ hơn được hiển thị ở trên (alternative_lighter): BẮT BUỘC lập luận để chuyển tội danh sang phương án nhẹ hơn đó. Dùng các điều khoản trong <legal_context> làm căn cứ. Đây là ưu tiên lập luận số 1.
● Nếu KHÔNG có phương án nhẹ hơn (alternative_lighter = null): Tội danh trên đã là đương nhiên không tranh cãi được. Chấp nhận tội danh, tập trung lập luận giảm nhẹ (tình tiết giảm nhẹ, dưới khung, án treo).

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
6. **TỪ VỰNG XƯNG HÔ (BẮT BUỘC):** Trong toàn bộ bài bào chữa, CHỈ ĐƯỢC DÙNG từ "thân chủ" để chỉ người bị buộc tội. TUYỆT ĐỐI KHÔNG sử dụng từ "bị cáo".
7. **PHÂN LOẠI TỘI PHẠM (BẮT BUỘC kiểm tra trước khi viết tình tiết giảm nhẹ):**
   - Tội ít nghiêm trọng: mức cao nhất của khung ≤ 03 năm tù.
   - Tội nghiêm trọng: mức cao nhất > 03 năm đến ≤ 07 năm tù.
   - Tội rất nghiêm trọng: mức cao nhất > 07 năm đến ≤ 15 năm tù.
   - Tội đặc biệt nghiêm trọng: mức cao nhất > 15 năm, chung thân hoặc tử hình.
   TUYỆT ĐỐI KHÔNG viết cụm "phạm tội lần đầu và thuộc trường hợp ít nghiêm trọng" nếu thân chủ bị truy tố theo khoản có mức cao nhất > 03 năm.
   Tình tiết giảm nhẹ "điểm i khoản 1 Điều 51" (phạm tội lần đầu) vẫn áp dụng được độc lập — chỉ cần xóa cụm "ít nghiêm trọng".
8. **ĐIỀU KIỆN BẮT BUỘC để áp dụng Điểm e Khoản 1 Điều 51 (Bị kích động do hành vi trái pháp luật của nạn nhân):**
   Hành vi của nạn nhân phải đủ NGHIÊM TRỌNG, ví dụ: tấn công trước, đe dọa tính mạng, xúc phạm danh dự trầm trọng, vi phạm pháp luật hình sự trực tiếp.
   TUYỆT ĐỐI KHÔNG áp dụng điểm e nếu nạn nhân chỉ: chửi thề thông thường, từ chối trả tiền, cự cãi bằng lời, dọa đánh mà chưa thực hiện hành vi tấn công.
   Tòa án sẽ bác lập luận này nếu không có căn cứ pháp lý vững. CHỈ viện dẫn điểm e khi đủ căn cứ.
9. **PHÚC TRÌNH TẠI ĐIỀU KHOẢN BỘ LUẬT ĐƯỢC TRÍCH DẪN:**
   - Tội danh chính (ví dụ: Điều 134): luôn trích dẫn phiên bản có hiệu lực tại THỜI ĐIỂM PHẠM TỘI (thường là BLHS 2015 sửa đổi 2017).
   - Tình tiết giảm nhẹ/tăng nặng (Điều 51, 52, 54, 65...): nếu phiên bản mới hơn có lợi hơn, áp dụng hồi tố và GHI RÕ "theo Nguyên tắc hồi tố có lợi — Điều 7 BLHS".
   - Nếu không rõ phiên bản nào có lợi hơn: mặc định dùng phiên bản tại thời điểm phạm tội.

⚠️ QUY TẮC CHỐNG THIÊN KIẾN (BẮT BUỘC — ĐỌC TRƯỚC KHI PHÂN TÍCH):
- KHÔNG THAY ĐỔI lập luận pháp lý chỉ vì người dùng phản đối, tỏ ra không hài lòng, hoặc hỏi lại bằng giọng điệu gay gắt.
- KHÔNG MÔ PHỎNG sự đồng ý giả tạo hoặc thêm câu "Bạn nói có lý" / "Tôi hiểu quan điểm của bạn" để xoa dịu.
- KHÔNG TỰ THÊM tuyên bố miễn trách nhiệm chưa được yêu cầu (ví dụ: "Tôi chỉ là AI, hãy hỏi luật sư thật").
- CHỈ thay đổi kết luận khi người dùng cung cấp: (a) tình tiết thực tế mới trong hồ sơ, HOẶC (b) điều luật cụ thể chưa được xem xét.
- Nếu bị phản đối mà không có bằng chứng mới: giữ nguyên kết luận, giải thích ngắn gọn căn cứ pháp lý.

QUY TRÌNH TƯ DUY BÀO CHỮA (BẮT BUỘC TOÀN BỘ):
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
  → Nếu thân chủ DƯỚI 18 TUỔI lúc phạm tội: BẮT BUỘC áp dụng Chương XII BLHS — mức hình phạt tối đa giảm ½ đến ¾.
BƯỚC 3: PHÂN TÍCH CẤU THÀNH TỘI PHẠM — tìm yếu tố nào còn thiếu hoặc chưa đủ để bác bỏ tội danh nặng hơn.
BƯỚC 4: PHÂN TÍCH TIỀN ÁN / TÁI PHẠM VÀ NHÂN THÂN (bắt buộc xử lý trung thực):
  - Tiền án (bản án hình sự): ảnh hưởng tái phạm nguy hiểm. Áp dụng quy tắc "tiêu hao tiền án" (xem phần map_laws).
  - Tiền sự (xử phạt hành chính): KHÔNG phải tiền án, nhưng vẫn là NHÂN THÂN XẤU mà Tòa án xét khi đánh giá khả năng cải tạo.
  BUỘC: Thừa nhận tiền sự trung thực, sau đó lập luận rằng tiền sự chỉ là vi phạm hành chính, không cấu thành tiền án hình sự và không dẫn đến tái phạm nguy hiểm.
  TUYỆT ĐỐI KHÔNG lờ đi hoặc cố tình xém nhẹ tiền sự, đặc biệt nếu tiền sự liên quan đến hành vi tương tự xảy ra sát thời điểm phạm tội.
  TUYỆT ĐỐI KHÔNG dùng lập luận "để xin giảm án sâu" dựa vào lý do "đây là lần đầu phạm tội" khi hồ sơ thể hiện nhiều tiền sự liên quan.
BƯỚC 5: LIỆT KÊ ĐẦY ĐỦ các tình tiết giảm nhẹ (Điều 46/51).
BƯỚC 6: LƯỢNG HÌNH — đề xuất mức hình phạt thấp nhất có thể biện hộ được.
  → Xem xét dưới khung (Điều 47/54) nếu ≥ 2 tình tiết giảm nhẹ và không tăng nặng.
  → Xem xét miễn hình phạt (Điều 25/59) nếu trường hợp đặc biệt.
  → Đề xuất án treo (Điều 60/65) nếu đủ 5 điều kiện.
BƯỚC 7: TỔNG HỢP HÌNH PHẠT (Điều 50/55) nếu nhiều tội.
BƯỚC 8: KHẤU TRỪ THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. PHÂN TÍCH PHÁP LÝ (GÓC ĐỘ BÀO CHỮA):**
1. **Kiểm tra loại trừ TNHS:** (phòng vệ chính đáng, tình thế cấp thiết, sự kiện bất ngờ...)
2. **Phân tích cấu thành tội phạm:** (có yếu tố nào còn thiếu không?)
3. **Tình tiết giảm nhẹ đề xuất:** (Điều 46/51 — liệt kê tất cả)
4. **Giai đoạn phạm tội:** (hoàn thành / chưa đạt — ảnh hưởng mức án)
5. **Vai trò đồng phạm:** (nếu có nhiều thân chủ)

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BÀO CHỮA:**
1. Đề nghị định tội danh: (tên tội + điều khoản cụ thể)
2. Áp dụng điều khoản: (liệt kê tất cả điều luật được viện dẫn)
3. HÌNH PHẠT ĐỀ NGHỊ — BẮT BUỘC nêu con số cụ thể:
   - Đề nghị xử phạt thân chủ [tên thân chủ]: [X năm Y tháng tù] hoặc [cải tạo không giam giữ X năm]
   - Nếu đề nghị án treo: nêu rõ mức tù cụ thể và thời gian thử thách (ví dụ: "01 năm tù nhưng cho hưởng án treo, thời gian thử thách 02 năm")
   - Nếu đề nghị dưới khung (Điều 54): giải thích căn cứ và mức cụ thể đề nghị
   TUYỆT ĐỐI KHÔNG để mơ hồ như "mức thấp nhất" hay "phù hợp" mà không kèm con số.
4. HÌNH PHẠT BỔ SUNG: (nếu có áp dụng)

**III. KHUYẾN NGHỊ CHO THÂN CHỦ:**
(Hướng dẫn bổ sung chứng cứ giảm nhẹ, thủ tục bồi thường, quyền kháng cáo...)

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Văn bản bộ luật hình sự | Lý do |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
"""
        elif role == "victim":
            prompt_template = """{role_instruction}

⛔ TUYỆT ĐỐI KHÔNG viết lời chào mở đầu như "Kính thưa Hội đồng xét xử", "Với kinh nghiệm X năm", hay bất kỳ phần giới thiệu bản thân nào. Bắt đầu NGAY VÀO NỘI DUNG phân tích pháp lý.

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy lập luận BẢO VỆ QUYỀN LỢI BỊ HẠI.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}

⚠️ TỘI DANH HỆ THỐNG XÁC ĐỊNH (căn cứ trung lập):
{mapped_context}

HƯỚNG DẪN SỬ DỤNG TỘI DANH TRONG VAI TRÒ BẢO VỆ BỊ HẠI (BẮT BUỘC TUÂN THỦ):
● Nếu phương án nặng hơn được hiển thị ở trên (alternative_heavier): BẮT BUỘC lập luận để chuyển tội danh sang phương án nặng hơn đó. Dùng các điều khoản trong <legal_context> làm căn cứ. Đây là ưu tiên lập luận số 1.
● Nếu KHÔNG có phương án nặng hơn (alternative_heavier = null): Tội danh trên đã là đương nhiên không tranh cãi được. Chấp nhận tội danh, tập trung lập luận tăng nặng, phản bác án treo, yêu cầu mức cao nhất trong khung.

{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
5. Phạm tội chưa đạt: Điều 18 Khoản 3 BLHS 1999 / Điều 15 + Điều 57 BLHS 2015 — áp dụng quy tắc ¾ mức cao nhất của khung.
6. **ĐIỀU KIỆN BẮt BUỘC của tình tiết tăng nặng “phạm tội đối với người ở trong tình trạng không thể tự vệ được” (Khoản 1 Điều 52):**
   Nạn nhân phải ở trạng thái hoàn toàn mất khả năng tự bảo vệ, ví dụ: đang ngủ say, bị trói, bị tê liệt, bất tỉnh, trẻ em nhỏ, người già yếu hoàn toàn không có khả năng chống cự.
   TUYỆT ĐỐI KHÔNG áp dụng tình tiết này chỉ vì nạn nhân đang ở tư thế yếu thế nhất thời (ngã, khom người, đang đứng dậy) trong một cuộc xô xát — đó là diễn biến thông thường của nhậu loạn, không đạt ngưỡng “không thể tự vệ” theo án lệ. Tòa án sẽ bác nếu áp dụng sai.
7. **ĐIỀU KIỆN BẮt BUỘC của tình tiết tăng nặng “dùng thủ đoạn hoặc phương tiện có khả năng gây nguy hại cho nhiều người” (Khoản 1 Điều 52):**
   Chỉ áp dụng khi bị cáo sử dụng công cụ có tính sát thương hàng loạt (mìn, thuốc độc bỏ vào thực phẩm, lái xe đâm đám đông, chất nổ...) với khả năng thực tế gây hại cho nhiều người cùng lúc.
   TUYỆT ĐỐI KHÔNG áp dụng nếu bị cáo chỉ dùng tay chân, vật thô thông thường đạnh một người duy nhất — dù nhắm vào vùng trọng yếu. Tòa án sẽ bác ngay lập luận này.

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
BƯỚC 4: LIỆT KÊ ĐẦY ĐỦ tình tiết tăng nặng (Điều 48/52) — CHỈ những tình tiết đáp ứng đầy đủ điều kiện pháp lý theo LƯ U Ý 6 và 7 ở trên.
BƯỚC 5: ÁN TREO — chỉ ra điều kiện nào của Điều 60/65 không thỏa mãn (nêu lý do không áp dụng án treo nếu có). 
BƯỚC 6: LƯỢNG HÌNH — đề nghị mức hình phạt cao nhất trong khung có căn cứ pháp lý.
BƯỚC 7: YÊU CẦU HÌNH PHẠT BỔ SUNG (tịch thu, cấm chức vụ, phạt tiền bổ sung nếu phù hợp).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. PHÂN TÍCH PHÁP LÝ (GÓC ĐỘ BẢO VỆ BỊ HẠI):**
1. **Xác nhận cấu thành tội phạm:** (khẳng định đủ 4 yếu tố)
2. **Tình tiết tăng nặng:** (Liệt kê CHỈ những tình tiết ĐÁP ỨNG ĐẦY ĐỦ điều kiện pháp lý theo LUẬT Ý 6 và 7 ở trên. Nếu tình tiết không đủ căn cứ thì GHI RÕ không áp dụng và lý do.)
3. **Phản bác án treo:** (chỉ ra điều kiện không thỏa mãn)

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BẢO VỆ BỊ HẠI:**
1. Đề nghị tuyên bố bị cáo phạm tội: (tên tội + điều khoản cụ thể)
2. Áp dụng điều khoản: (liệt kê tất cả điều luật được viện dẫn)
3. HÌNH PHẠT ĐỀ NGHỊ — BẮT BUỘC nêu con số cụ thể:
   - Đề nghị xử phạt bị cáo [tên bị cáo]: [X năm tù giam] (mức cao nhất trong khung)
   - Không cho hưởng án treo: (nêu rõ lý do phản bác án treo nếu có đề nghị từ phía bào chữa)
   TUYỆT ĐỐI KHÔNG để mơ hồ như "mức cao nhất" hay "xử phạt nhiều năm" mà không kèm con số.
4. YÊU CẦU BỒI THƯỜNG THIỆT HẠI:
   - Căn cứ pháp lý: viện dẫn điều khoản BLHS quy định nghĩa vụ bồi thường (ví dụ: Điều 42 BLHS 1999 hoặc Điều 48 BLHS 2015 tùy theo thời điểm phạm tội).
   - Đề nghị Hội đồng xét xử buộc bị cáo bồi thường toàn bộ thiệt hại thực tế đã gây ra cho bị hại theo các hóa đơn, chứng từ hợp pháp mà gia đình bị hại sẽ cung cấp cho Tòa án.
   - TUYỆT ĐỐI KHÔNG tự đặt ra bất kỳ con số tiền bồi thường cụ thể nào. KHÔNG được viết "X triệu đồng", "Y tỷ đồng", hay bất kỳ số tiền ước tính nào — Tòa án sẽ định lượng dựa trên hồ sơ thực tế.
5. HÌNH PHẠT BỔ SUNG: (nếu áp dụng được)

**III. KHUYẾN NGHỊ CHO GIA ĐÌNH BỊ HẠI:**
(Hướng dẫn bảo vệ quyền lợi trong quá trình tố tụng, quyền kháng cáo bản án nếu chưa thỏa đáng, liên hệ cơ quan tiến hành tố tụng để được cập nhật tiến độ vụ án và bảo đảm quyền lợi của bị hại...)

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Văn bản bộ luật hình sự | Lý do |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
"""
        else:  # neutral — judge perspective
            prompt_template = """{judge_charge_block}
{role_instruction}

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy ra PHÁN QUYẾT CỤ THỂ.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}

⚖️ TỘI DANH HỆ THỐNG XÁC ĐỊNH (BẮT BUỘC SỬ DỤNG LÀM CĂN CỨ XÉT XỬ):
{mapped_context}

HƯỚNG DẪN CHO THẨM PHÁN (BUỘC TUÂN THỦ):
● Tội danh và điều khoản được xác định ở trên là kết quả phân tích trung lập — TÒA ÁN BẮT BUỘC SỬ DỤNG đây làm căn cứ DUY NHẤT để định tội. KHÔNG đề xuất, so sánh, hay trình bày bất kỳ phương án tội danh thay thế nào khác (không có "Phương án 1", "Phương án 2").
● TUYỆT ĐỐI KHÔNG tự thay đổi tội danh sang một điều khoản hoàn toàn khác không có trong mapped_context hay legal_context.
● Nhiệm vụ duy nhất của Tòa án là: xác nhận tội danh đã được xác định, phân tích đủ 4 yếu tố cấu thành, đánh giá tình tiết tăng nặng/giảm nhẹ, rồi ra QUYẾT ĐỊNH mức hình phạt cụ thể.

{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 Bộ luật Hình sự mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế / Tội ít nghiêm trọng: NẾU điều luật có quy định hình phạt chính là "phạt tiền" (bên cạnh phạt tù) VÀ bị cáo có nhiều tình tiết giảm nhẹ (như khắc phục hậu quả, thành khẩn khai báo) → BẮT BUỘC Tòa án phải ưu tiên xem xét áp dụng phạt tiền làm hình phạt chính thay vì phạt tù, nhằm đảm bảo tính nhân đạo và thu hồi tài sản cho Nhà nước.
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
4. ÁN PHÍ: 200.000 đồng

**ĐIỀU KHOẢN ÁP DỤNG:**
(Bảng tổng hợp — CHỈ liệt kê các điều luật đã được trích dẫn CỤ THỂ trong nội dung phân tích ở trên. TUYỆT ĐỐI KHÔNG thêm điều luật chưa được đề cập. BẮT BUỘC trình bày bảng đúng chuẩn Markdown, phải có ĐÚNG 4 cột và hàng phân cách phải đủ 4 cột `|---|---|---|---|`. QUY TẮC GỘP DÒNG BẮT BUỘC: Mỗi SỐ ĐIỀU chỉ được xuất hiện ĐÚNG MỘT HÀNG duy nhất — nếu một điều được viện dẫn ở nhiều khoản hoặc điểm khác nhau, hãy gộp tất cả vào một hàng, liệt kê các khoản/điểm trong cột Tội danh/Nội dung, ví dụ: "Khoản 1; Khoản 2 điểm g".)


| Điều | Tội danh/Nội dung | Nguồn áp dụng | Lý do chọn nguồn |
|---|---|---|---|
| (số điều) | (nội dung) | (tên bộ luật + năm) | (lý do áp dụng) |
"""

        prompt = ChatPromptTemplate.from_template(prompt_template)

        # NOTE: history is intentionally NOT injected here.
        # generate_node is only reachable via the new_case branch
        # (START → extract_facts → ... → map_laws → generate).
        # Injecting chat_history from a prior case would contaminate
        # the fresh analysis with irrelevant messages.
        # History-aware responses are handled solely by followup_generate_node.

        try:
            fmt_kwargs = dict(
                role_instruction=role_instruction,
                context=context_text,
                case_details=case_details,
                deterministic_context=det_context,
                mapped_context=mapped_context,
                nhan_than_context=nhan_than_context,
            )
            if role == "neutral":
                fmt_kwargs["judge_charge_block"] = judge_charge_block
            formatted_prompt = prompt.format_messages(**fmt_kwargs)
            final_messages = formatted_prompt
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
                f"Bạn nên tự kiểm tra lại {arts} "
                f"trong văn bản luật gốc để xác nhận điều khoản này áp dụng cho vụ án."
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
                f"Bạn nên kiểm tra lại phiên bản Bộ luật Hình sự áp dụng cho {', '.join(wrong_edition)}: "
                f"với ngày phạm tội {crime_date}, phiên bản phù hợp là {correct}."
            )
            print(f"  [VERIFY L1-B] ❌ Wrong edition(s) cited: {wrong_edition} "
                  f"(expected: {correct})")
        else:
            print("  [VERIFY L1-B] ✅ Temporal edition check passed.")

        # L1-C: Role signal check
        role_score = _verify_role_signal(ai_text, role)
        if role_score < 0.35:
            issues.append(
                f"Bạn nên xem xét lại góc độ lập luận để phù hợp hơn với vai '{role}' "
                f"khi vận dụng phân tích này vào thực tiễn."
            )
            print(f"  [VERIFY L1-C] ❌ Role signal weak: score={role_score:.2f} "
                  f"(threshold=0.35) for role='{role}'")
        else:
            print(f"  [VERIFY L1-C] ✅ Role signal OK: score={role_score:.2f} "
                  f"for role='{role}'")

        # Layer 2 — LLM Judge (skip if L1 already caught >= 2 issues)
        if len(issues) < 2:
            print(f"  [VERIFY L2] Triggering LLM judge (L1 issues so far: {len(issues)})")
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
                "- factual_ok = false CHỈ KHI hệ thống tự tạo ra chi tiết cụ thể (số tiền, số bản án, ngày tháng cụ thể) KHÔNG có trong NGUYÊN VĂN VỤ ÁN.\n"
                "- Nếu thông tin có trong NGUYÊN VĂN VỤ ÁN nhưng không có trong Bản tóm tắt thì VẪN HỢP LỆ (factual_ok = true).\n"
                "- role_ok = false CHỈ KHI hệ thống rõ ràng lập luận SAI chiều với vai trò được giao.\n"
                "- Nếu không chắc → true (tránh false positive).\n"
                "- TUYỆT ĐỐI KHÔNG đề cập tên trường kỹ thuật trong mô tả.\n"
                "- TUYỆT ĐỐI KHÔNG dùng từ \"bịa\" hoặc \"bịa đặt\" trong factual_issue. Hãy dùng cụm \"hệ thống có thể đã không chính xác trong [chi tiết]\".\n"
                "- TUYỆT ĐỐI KHÔNG dùng từ \"AI\" trong bất kỳ trường nào. Luôn dùng \"hệ thống\" thay thế.\n"
                "- Viết mô tả NGẮN GỌN tối đa 15 từ, lịch sự và dễ hiểu cho người dùng thông thường.\n"
                "OUTPUT: Chỉ JSON hợp lệ, không markdown."
            )

            # ── Debug: log the full prompt sent to the LLM judge ─────────────
            print("  [VERIFY L2] ── JUDGE PROMPT ──────────────────────────────")
            print(f"  [VERIFY L2] role          = {role!r}")
            print(f"  [VERIFY L2] role_desc     = {role_map.get(role, role)!r}")
            print(f"  [VERIFY L2] fact_summary  =\n{fact_summary}")
            print(f"  [VERIFY L2] response_snippet (first 300 chars):\n{response_snippet[:300]!r}")
            print("  [VERIFY L2] ───────────────────────────────────────────────")

            try:
                judge_llm = llm.bind(max_tokens=400)
                judge_resp = judge_llm.invoke(
                    _sanitize_msgs([HumanMessage(content=judge_prompt)])
                )

                # ── Debug: log raw LLM response ───────────────────────────────
                print(f"  [VERIFY L2] Raw LLM judge response: {judge_resp.content!r}")

                verdict = _extract_json(judge_resp.content)

                # ── Debug: log parsed verdict ─────────────────────────────────
                print(f"  [VERIFY L2] Parsed verdict: {verdict}")

                factual_ok  = verdict.get("factual_ok", True)
                factual_msg = verdict.get("factual_issue")
                role_ok     = verdict.get("role_ok", True)
                role_msg    = verdict.get("role_issue")

                if not factual_ok and factual_msg:
                    print(
                        f"  [VERIFY L2] ❌ FACTUAL issue detected.\n"
                        f"    → factual_ok  = {factual_ok}\n"
                        f"    → factual_issue = {factual_msg!r}\n"
                        f"    → Why: LLM judge determined the AI response contained "
                        f"specific details not found in the original case text or extracted facts."
                    )
                    issues.append(f"Bạn nên xác nhận lại thông tin sau với hồ sơ gốc: {factual_msg}")
                else:
                    print(f"  [VERIFY L2] ✅ Factual consistency OK (factual_ok={factual_ok})")

                if not role_ok and role_msg:
                    print(
                        f"  [VERIFY L2] ❌ ROLE ADHERENCE issue detected.\n"
                        f"    → role_ok    = {role_ok}\n"
                        f"    → role_issue = {role_msg!r}\n"
                        f"    → Why: LLM judge determined the hệ thống response argued in the "
                        f"wrong direction for the assigned role '{role}' "
                        f"({role_map.get(role, role)})."
                    )
                    issues.append(f"Bạn nên cân nhắc điều chỉnh góc lập luận cho phù hợp hơn với vai '{role}': {role_msg}")
                else:
                    print(f"  [VERIFY L2] ✅ Role adherence OK (role_ok={role_ok})")

            except Exception as judge_err:
                print(
                    f"  [VERIFY L2] ❌ LLM judge call FAILED\n"
                    f"    → Exception type : {type(judge_err).__name__}\n"
                    f"    → Exception msg  : {judge_err}\n"
                    f"    → Action         : Skipping L2, final answer proceeds without L2 check."
                )
        else:
            print(
                f"  [VERIFY L2] Skipped — L1 already found {len(issues)} issue(s): "
                + "; ".join(issues)
            )

        if not issues:
            print("  ✅ Answer verification passed — no issues detected.")
            return {}

        warning_block = (
            "\n\n---\n"
            "**Lưu ý khi tham khảo:**\n"
            + "\n".join(f"- {i}" for i in issues)
            + "\n\n*Phân tích trên mang tính tham khảo. Để đảm bảo chính xác, bạn nên đối chiếu với văn bản luật gốc và tư vấn luật sư có thẩm quyền.*"
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

        # ── MemorySaver State Dump ──────────────────────────────────────────────
        # Prints what was restored from checkpoint vs what came from the current
        # request. Use this to verify MemorySaver is working correctly.
        _facts        = state.get("extracted_facts") or {}
        _docs         = state.get("documents") or []
        _full_case    = state.get("full_case_content") or ""
        _per_def      = state.get("per_defendant_dates") or []
        _sentencing   = state.get("sentencing_data") or {}
        print("  ┌─ [FOLLOWUP] MemorySaver State Dump ─────────────────────────")
        print(f"  │  question (current turn) : '{question[:80]}'")
        print(f"  │  user_role               : {role}")
        print(f"  │  chat_history turns      : {len(full_history)}")
        print(f"  │  ── Checkpoint-restored fields ──")
        print(f"  │  full_case_content       : '{_full_case[:80]}...' ({len(_full_case)} chars)")
        print(f"  │  extracted_facts fields  : {list(_facts.keys()) if _facts else '(empty)'}")
        print(f"  │  mapped_laws             : {len(mapped_laws)} entries → {[f'Điều {m.get(\"article_number\",\"?\")}' for m in mapped_laws[:5]]}")
        print(f"  │  documents               : {len(_docs)} docs → {[f'Điều {d.metadata.get(\"article_number\",\"?\")} ({d.metadata.get(\"source\",\"?\")})' for d in _docs[:5]]}")
        print(f"  │  per_defendant_dates     : {len(_per_def)} defendants")
        print(f"  │  sentencing_data keys    : {list(_sentencing.keys()) if _sentencing else '(empty)'}")
        print("  └─────────────────────────────────────────────────────────────")

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
