"""
graph/nodes/law_mapping.py — VNPLaw AI Service
Node 7: map_laws_node
"""
import re
import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.schemas import AgentState
from app.utils.clean_json import _extract_json
from app.utils.text import _sanitize_msgs


def make_map_laws_node(llm, measure_time):
    """Factory: returns map_laws_node bound to 'llm'."""

    @measure_time('map_laws')
    def map_laws_node(state: AgentState) -> dict:
        """Map extracted facts to specific law articles."""
        print("[NODE: map_laws]")
        facts     = state.get("extracted_facts") or {}
        documents = state.get("documents") or []
        case_text = state.get("full_case_content") or state.get("question", "")

        if not documents:
            print("⚠️  map_laws: no documents in state — returning error sentinel")
            return {"mapped_laws": [{
                "article": "N/A", "clause": "N/A",
                "offense_name": "Không xác định được",
                "applicable_reason": "Không có tài liệu luật nào được trích xuất.",
                "edition_applied": "N/A", "edition_reason": "Không có tài liệu.",
                "_mapping_error": True,
            }]}

        context = "\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','?')} "
            f"| role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ])
        facts_str = json.dumps(facts, ensure_ascii=False, indent=2)

        system_prompt = """Bạn là chuyên gia luật hình sự Việt Nam.
Ánh xạ từng hành vi phạm tội vào điều khoản cụ thể, áp dụng ĐÚNG nguyên tắc hiệu lực của luật.

❌ NGHIÊM CẤM: Chỉ được trích dẫn điều khoản thuộc BỘ LUẬT HÌNH SỰ (BLHS).
KHÔNG được áp dụng bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự,
Bộ luật Lao động, hôn nhân gia đình, hay bất kỳ bộ luật, nghị định, thông tư nào khác.

❌ NGHIÊM CẤM: CHỈ được ánh xạ vào các ĐIỀU LUẬT có số hiệu XUẤT HIỆN TRONG VĂN BẢN LUẬT ĐÃ CUNG CẤP Ở TRÊN.
NGHIÊM CẤM truy xuất bất kỳ số điều nào từ kiến thức nội tại (training knowledge) không có trong tài liệu trên.
Nếu tài liệu cung cấp không chứa điều luật phù hợp, chỉ ánh xạ đến những gì có trong tài liệu và ghi rõ hạn chế này trong applicable_reason.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BƯỚC 0 — PHÂN TÍCH Ý ĐỊNH CHỦ QUAN — BẮT BUỘC TRƯỚC KHI ÁNH XẠ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ NGUYÊN TẮC NỀN TẢNG: Tội danh (điều luật áp dụng) được xác định bởi Ý ĐỊNH của bị cáo,
KHÔNG phải kết quả thực tế. Kết quả thực tế (tỷ lệ thương tích, số tiền thiệt hại, nạn nhân
có chết hay không...) CHỈ ảnh hưởng đến:
  (a) Khoản áp dụng trong điều luật đã xác định (Khoản 1 / 2 / 3), HOẶC
  (b) Giai đoạn phạm tội (hoàn thành / chưa đạt / chuẩn bị — áp dụng Điều 15, 57 BLHS).
NGHIÊM CẤM dùng kết quả thực tế là CĂN CỨ CHÍNH để xác định tội danh.

QUY TRÌNH BẮT BUỘC (thực hiện tuần tự trước khi viết JSON):

Bước 0.1 — XÁC ĐỊNH Ý ĐỊNH (Intent):
  Bị cáo muốn đạt được điều gì thông qua hành vi? Trả lời một trong các loại sau:
  - Tước đoạt tính mạng nạn nhân?
  - Gây thương tích / đau đớn (không nhất thiết muốn chết)?
  - Chiếm đoạt tài sản?
  - Thỏa mãn ham muốn tình dục?
  - Vi phạm quy tắc/quy định (không có chủ đích gây hại)?
  Nếu ý định KHÔNG được nêu rõ trong hồ sơ → chuyển sang Bước 0.2.

Bước 0.2 — SUY LUẬN Ý ĐỊNH TỪ HÀNH VI KHÁCH QUAN (khi ý định không nêu rõ):
  Đặt câu hỏi: "Tại THỜI ĐIỂM THỰC HIỆN HÀNH VI, bị cáo có BIẾT hành vi này có thể gây chết người không?"
  Nếu CÓ → ý định là giết người (dù cố ý trực tiếp hay gián tiếp).

  ⚠️ QUY TẮC CỐ Ý GIÁN TIẾP (CRITICAL):
  Điều 123 bao gồm cả lỗi CỐ Ý GIÁN TIẾP: Bị cáo không nhất thiết MUỐN nạn nhân chết,
  nhưng đã thực hiện hành vi mà bản thân BIẾT CÓ THỂ GÂY CHẾT NGƯỜI và đã CHẤP NHẬN hậu quả đó.
  → Ví dụ: dùng búa bổ củi đánh vào thái dương (vùng trọng yếu) = biết có thể gây chết = cố ý gián tiếp = Điều 123.

  🚫 HAI SAI LẦM PHẢI TRÁNH TUYỆT ĐỐI:

  SAI LẦM 1 — DÙNG HÀNH VI SAU PHẠM TỘI ĐỂ XÁC ĐỊNH Ý ĐỊNH:
  Hành vi SAU KHI phạm tội (tự gây thương tích, bỏ trốn, gọi cấp cứu, tự thú, hối hận...)
  TUYỆT ĐỐI KHÔNG được dùng để suy luận về ý định TẠI THỜI ĐIỂM THỰC HIỆN HÀNH VI.
  Ý định phải được xác định hoàn toàn dựa trên: vũ khí + vị trí tấn công + cách thức hành động TẠI THỜI ĐIỂM PHẠM TỘI.
  ★ Ví dụ sai: "Bị cáo tự đâm bụng sau khi đánh nạn nhân → chứng tỏ không muốn nạn nhân chết → Điều 134"
    → ĐÂY LÀ SUY LUẬN SAI. Hành vi tự đâm bụng sau đó là biểu hiện hối hận, KHÔNG phủ nhận ý định tại thời điểm tấn công.

  SAI LẦM 2 — COI KẾT LUẬN GIÁM ĐỊNH PHÁP Y TÂM THẦN LÀ XÁC ĐỊNH TỘI DANH:
  Kết luận giám định pháp y tâm thần chỉ xác định NĂNG LỰC TRÁCH NHIỆM HÌNH SỰ
  (bị cáo có đủ khả năng nhận thức và điều khiển hành vi không).
  Khi kết luận giám định dùng từ ngữ như "cố ý gây thương tích" — đây là MÔ TẢ HÀNH VI ĐƯỢC CÁO BUỘC tại thời điểm giám định,
  KHÔNG PHẢI xác định tội danh pháp lý. Tội danh do Tòa án xác định, không phải giám định viên.
  ★ Ví dụ sai: "Kết luận giám định nói 'cố ý gây thương tích' → nên ánh xạ Điều 134"
    → ĐÂY LÀ SUY LUẬN SAI. Giám định viên không có thẩm quyền định tội.

  Áp dụng các suy luận ý định từ hành vi khách quan:
  - Công cụ/vũ khí sát thương cao (dao nhọn, búa, rìu, gậy sắt, súng...) + nhắm vào
    VÙNG TRỌNG YẾU (đầu, thái dương, cổ, ngực, bụng)
    → SUY LUẬN: Cố ý giết người (cố ý gián tiếp tối thiểu). Nạn nhân sống chỉ là tội chưa đạt, KHÔNG thay đổi tội danh.
  - Đánh/đấm tay không hoặc vật thô vào vùng KHÔNG trọng yếu (tay, chân, vai, lưng)
    → SUY LUẬN: Cố ý gây thương tích.
  - Lái xe không tuân thủ luật, không có ý định đâm người
    → SUY LUẬN: Vô ý (vi phạm quy định giao thông, Điều 260).
  - Dùng vũ lực trực tiếp hoặc đe dọa dùng vũ lực ngay tức khắc để chiếm tài sản
    → SUY LUẬN: Cướp tài sản (Điều 168), KHÔNG phải trộm cắp hay cưỡng đoạt.
  - Gian dối để tạo niềm tin TRƯỚC khi nhận tài sản
    → SUY LUẬN: Lừa đảo chiếm đoạt tài sản (Điều 174).
  - Nhận tài sản hợp pháp, sau đó mới bỏ trốn/gian dối/tiêu xài hết
    → SUY LUẬN: Lạm dụng tín nhiệm chiếm đoạt tài sản (Điều 175).

Bước 0.3 — XÁC ĐỊNH GIAI ĐOẠN PHẠM TỘI (tách biệt hoàn toàn khỏi loại tội):
  - Ý định: giết người → nạn nhân SỐNG
    → Tội GIẾT NGƯỜI chưa đạt. Áp dụng Điều 123 + Điều 15 + Điều 57 khoản 3.
    ★ TUYỆT ĐỐI KHÔNG hạ xuống Điều 134 chỉ vì nạn nhân sống sót.
    ★ TUYỆT ĐỐI KHÔNG dùng hành vi hối hận sau đó để hạ tội danh xuống Điều 134.
  - Ý định: cướp tài sản → chưa lấy được tài sản
    → Tội CƯỚP chưa đạt. Áp dụng Điều 168 + Điều 15 + Điều 57.
  - Ý định: gây thương tích → nạn nhân bị thương thực tế
    → Hoàn thành. Chọn khoản theo % thương tích trong Điều 134.

Bước 0.4 — CHỈ SAU KHI ĐÃ XÁC ĐỊNH Ý ĐỊNH VÀ GIAI ĐOẠN, mới đọc % thương tích / số
  tiền thiệt hại để chọn khoản trong điều luật đã xác định.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BẢNG CÁC CẶP ĐIỀU LUẬT THƯỜNG BỊ NHẦM — ĐỌC TRƯỚC KHI ÁNH XẠ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[NHÓM 1 — TỘI XÂM PHẠM TÍNH MẠNG, SỨC KHỎE]
• Điều 123 vs Điều 134:
  → Phân biệt bằng Ý ĐỊNH và HÀNH VI, KHÔNG phải % thương tích.
  → Vũ khí sát thương + vùng trọng yếu = Cố ý giết người.
  → Nạn nhân sống: Điều 123 chưa đạt. KHÔNG hạ xuống Điều 134.
  → Tình tiết "có tính chất côn đồ" (điểm n khoản 1 Điều 123): hành vi hung hãn, bột phát, coi
    thường tính mạng người khác dù không có thù oán từ trước.
    ★ DẤU HIỆU CÔN ĐỒ: Bị cáo dùng hung khí tấn công người thân/quen chỉ vì lý do rất nhỏ nhặt
    (bị mắng, tranh cãi nhỏ, va chạm giao thông...). Đây LÀ hành vi côn đồ → BẮT BUỘC xét khoản 1.
    ★ Khi đã xác định côn đồ → ánh xạ Khoản 1 Điều 123, KHÔNG ánh xạ Khoản 2.

[NHÓM 2 — TỘI XÂM PHẠM TÀI SẢN]
• Điều 168 vs Điều 170 vs Điều 171 vs Điều 173:
  → Điều 168 (Cướp): Vũ lực TRỰC TIẾP hoặc đe dọa dùng vũ lực NGAY TỨC KHẮC → làm tê liệt ý chí.
  → Điều 170 (Cưỡng đoạt): Đe dọa từ xa / không thể thực hiện ngay / đe dọa tố cáo.
  → Điều 171 (Cướp giật): Công khai chiếm đoạt nhanh, lợi dụng sơ hở, không vũ lực trực tiếp.
  → Điều 173 (Trộm cắp): Lén lút, không để nạn nhân hay người xung quanh biết.

• Điều 174 vs Điều 175:
  → Điều 174 (Lừa đảo): Gian dối TRƯỚC hoặc ĐỒNG THỜI khi nhận tài sản.
  → Điều 175 (Lạm dụng tín nhiệm): Nhận tài sản hợp pháp trước, SAU ĐÓ mới chiếm đoạt.

[NHÓM 3 — TỘI XÂM PHẠM TÌNH DỤC]
• Điều 141 vs Điều 143 vs Điều 145:
  → Điều 141 (Hiếp dâm): Dùng vũ lực / đe dọa dùng vũ lực / lợi dụng không thể kháng cự.
  → Điều 143 (Cưỡng dâm): Lợi dụng quan hệ lệ thuộc / khó khăn để ép buộc (không vũ lực trực tiếp).
  → Điều 145: Nạn nhân từ đủ 13 đến dưới 16 tuổi — kể cả thuận tình (tuổi quyết định, không phải vũ lực).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUY TẮC KHOẢN — BẮT BUỘC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Mỗi hành vi phạm tội CHỈ được ánh xạ vào ĐÚNG MỘT khoản duy nhất (khoản áp dụng trực tiếp).
- KHÔNG được liệt kê cùng một điều luật ở nhiều khoản khác nhau cho cùng một hành vi.

🚫 KIỂM TRA "TIÊU HAO TIỀN ÁN" — BẮT BUỘC TRƯỚC KHI XEM XÉT KHOẢN CAO HƠN:
  - Nếu giá trị tài sản DƯỚI NGƯỠNG cơ bản (ví dụ: trộm cắp < 2.000.000đ): hành vi chỉ cấu thành
    tội phạm nhờ tiền án "đã bị kết án, chưa được xóa án tích mà còn vi phạm" (Khoản 1).
    Tiền án đó bị TIÊU HAO tại Khoản 1.
    → KHÔNG CÓ CON ĐƯỜNG NÀO ĐI THẲNG LÊN KHOẢN 2.
    → BẮT BUỘC ánh xạ vào Khoản 1. KHÔNG ánh xạ vào Khoản 2.
  - Chỉ được xét Khoản 2 khi giá trị tài sản ĐÃ ĐỦ cấu thành tội phạm độc lập (>= 2.000.000đ).
    Khi đó tiền án còn "tự do" và được phép kích hoạt tình tiết định khung tại Khoản 2.
  ★ Ví dụ: trộm cắp 476.000đ + có tiền án 05/2022 chưa xóa → tiền án tiêu hao ở Khoản 1
    → ánh xạ Khoản 1, KHÔNG Khoản 2.

- SAU KHI xác nhận giá trị >= ngưỡng (tiền án còn tự do): NẾU sự kiện có tình tiết tăng nặng
  ("tái phạm nguy hiểm", "có tổ chức", "chuyên nghiệp"...), ĐỌC KỸ từng khoản của điều luật.
- Nếu khoản cao hơn CÓ CHÍNH THỨC quy định tình tiết đó → BẮT BUỘC ánh xạ vào khoản cao hơn.
- Nếu KHÔNG quy định → giữ khoản cơ bản, tình tiết đó chỉ là tình tiết tăng nặng chung.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NGUYÊN TẮC THỜI HIỆU (Điều 7 BLHS) — BẮT BUỘC ÁP DỤNG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. QUY TẮC CƠ BẢN: Áp dụng luật có hiệu lực tại THỜI ĐIỂM PHẠM TỘI (tài liệu có role=primary).
2. NGOẠI LỆ HỒI TỐ CÓ LỢI: Nếu luật MỚI HƠN (role=comparison) quy định hình phạt NHẸ HƠN,
   BẮT BUỘC áp dụng.
3. NGHIÊM CẤM hồi tố nếu luật mới NẶNG HƠN — giữ luật cũ.
4. ĐA TỘI DANH: So sánh từng tội danh riêng biệt.
5. ĐA BỊ CÁO: Mỗi bị cáo xét theo ngày họ thực hiện hành vi.

Trả về JSON array:
[
  {
    "article": "Điều 168",
    "clause": "Khoản 2",
    "offense_name": "Tội cướp tài sản",
    "applicable_reason": "Lý do áp dụng (bao gồm kết quả phân tích ý định chủ quan từ Bước 0)",
    "edition_applied": "BLHS 2015 (sửa đổi 2017)",
    "edition_reason": "Nếu KHÔNG có tài liệu comparison: 'Áp dụng luật có hiệu lực tại thời điểm phạm tội'. Nếu CÓ tài liệu comparison: 'Áp dụng luật tại thời điểm phạm tội do luật mới không có lợi hơn'.",
    "alternative_lighter_article": null,
    "alternative_heavier_article": null
  }
]

QUY TẮC XÁC ĐỊNH alternative_lighter_article và alternative_heavier_article:

🚫 RÀNG BUỘC BẮT BUỘC:
- alternative_lighter_article và alternative_heavier_article CHỈ ĐƯỢC điền các điều luật quy định TỘI DANH CỤ THỂ (tội phạm thực thể).
- TUYỆT ĐỐI KHÔNG điền các điều luật chung/thủ tục như: Điều 57 (phạm tội chưa đạt), Điều 15 (chuẩn bị), Điều 50 (căn cứ quyết định hình phạt), Điều 51/52 (tình tiết giảm/tăng nặng), Điều 7 (hiệu lực luật)...
- Nếu KHÔNG có điều luật tội danh nào nhẹ hơn/nặng hơn phù hợp trong tài liệu đã cung cấp → BẮT BUỘC để null.

► alternative_lighter_article: Bất kỳ luật sư bào chữa có thể lập luận hợp lệ để định tội sang điều khoản TỘI DANH CỤ THỂ NHẸ HƠN không?
  - Nếu CÓ căn cứ pháp lý trong tài liệu đã cung cấp: điền {"article": "Điều X", "clause": "Khoản Y", "offense_name": "...", "reason": "lý do ngắn gọn"}
  - Nếu KHÔNG có căn cứ (tội danh đã rõ ràng, không thể tranh luận xuống dưới): null
  Ví dụ: Ánh xạ vào Điều 123 (giết người) nhưng bước 0 chưa xác định rõ vũ khí → luật sư có thể tranh luận xuống Điều 134 → alternative_lighter = {"article": "Điều 134", "clause": "Khoản 3", "offense_name": "Cố ý gây thương tích", "reason": "Nếu hội đồng xét xử cho rằng chưa đủ căn cứ về ý định giết người"}

► alternative_heavier_article: Bất kỳ luật sư bị hại có thể lập luận hợp lệ để định tội sang điều khoản TỘI DANH CỤ THỂ NẶNG HƠN không?
  - Nếu CÓ căn cứ pháp lý trong tài liệu đã cung cấp: điền {"article": "Điều X", "clause": "Khoản Y", "offense_name": "...", "reason": "lý do ngắn gọn"}
  - Nếu KHÔNG có căn cứ (tội danh đã rõ ràng, không thể tranh luận lên trên): null
  Ví dụ: Ánh xạ vào Điều 170 (cưỡng đoạt) nhưng bị hại thấy bị cáo đã dùng vũ lực trực tiếp → có thể tranh lên Điều 168 (cướp) → alternative_heavier = {"article": "Điều 168", ...}

Chỉ điền các lựa chọn alternative khi CHÚNG CÓ TRONG tài liệu đã cung cấp. TUYỆT ĐỐI KHÔNG bịa đặt điều luật không có trong context.
OUTPUT: CHỈ JSON array hợp lệ."""

        try:
            response = llm.invoke(_sanitize_msgs([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"SỰ KIỆN:\n{facts_str}\n\nVĂN BẢN LUẬT (có nhãn role):\n{context}\n\nVỤ ÁN:\n{case_text}")
            ]))
            raw_output = response.content
            print(f"  [MAP_LAWS] LLM output:\n{raw_output}")

            mapped = _extract_json(raw_output)
            if isinstance(mapped, dict):
                mapped = [mapped]
            if not isinstance(mapped, list) or len(mapped) == 0:
                raise ValueError("Empty or non-list mapped_laws")

            # STRICT FILTER: Drop any mapped article that is NOT in retrieved documents
            retrieved_articles = {str(d.metadata.get("article_number", "")) for d in documents}
            valid_mapped = []
            for item in mapped:
                raw_art = str(item.get("article", ""))
                match = re.search(r"\d+", raw_art)
                if match:
                    art_num = match.group(0)
                    if art_num in retrieved_articles:
                        valid_mapped.append(item)
                    else:
                        print(f"  [MAP_LAWS] ❌ Dropping hallucinated article: Điều {art_num} (not in retrieved docs)")
                else:
                    valid_mapped.append(item)

            if not valid_mapped:
                print("  [MAP_LAWS] ⚠️  All articles dropped by hallucination filter — returning error sentinel")
                valid_mapped = [{
                    "article": "N/A", "clause": "N/A",
                    "offense_name": "Không xác định được",
                    "applicable_reason": "Tài liệu truy xuất không chứa điều luật phù hợp để ánh xạ tội danh này.",
                    "edition_applied": "N/A", "edition_reason": "",
                    "_mapping_error": True,
                }]
            mapped = valid_mapped

        except Exception as e:
            print(f"⚠️  Law mapping failed: {type(e).__name__}: {e}")
            mapped = [{
                "article": "N/A", "clause": "N/A",
                "offense_name": "Không xác định được",
                "applicable_reason": "Hệ thống không thể ánh xạ điều luật từ các tài liệu đã trích xuất.",
                "edition_applied": "N/A",
                "edition_reason": "Lỗi phân tích pháp luật.",
                "_mapping_error": True,
            }]

        if mapped and not mapped[0].get("_mapping_error"):
            print(f"  [MAP_LAWS] ✅ {len(mapped)} offense(s) mapped:")
            for m in mapped:
                print(f"    → {m.get('article','?')} {m.get('clause','?')} | {m.get('offense_name','?')} | {m.get('edition_applied','?')}")
        else:
            print("  [MAP_LAWS] ❌ Mapping error — check LLM output or retrieval quality")
        return {"mapped_laws": mapped}

    return map_laws_node
