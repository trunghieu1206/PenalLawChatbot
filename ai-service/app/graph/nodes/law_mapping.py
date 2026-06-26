"""
graph/nodes/law_mapping.py — VNPLaw AI Service
Node 7: map_laws_node
"""
import re
import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.schemas import AgentState
from app.utils.legal import _extract_json
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

QUY TẮC KHOẢN — BẮT BUỘC:
- Mỗi hành vi phạm tội CHỈ được ánh xạ vào ĐÚNG MỘT khoản duy nhất (khoản áp dụng trực tiếp).
- KHÔNG được liệt kê cùng một điều luật ở nhiều khoản khác nhau cho cùng một hành vi.

🚫 KIỂM TRA "TIÊU HAO TIỀN ÁN" — BẮT BUỘC TRƯỚC KHI XEM XÉT KHOẢN CAO HƠN:
  Bước 0: Xét giá trị tài sản / hậu quả của hành vi hiện tại.
  - Nếu giá trị ĐÓ DƯỚI NGƯỠNG cơ bản (ví dụ: trộm cắp < 2.000.000đ): hành vi chỉ cấu thành tội phạm nhờ tiền án
    "đã bị kết án, chưa được xóa án tích mà còn vi phạm" (Khoản 1). Tiền án đó bị TIÊU HAO tại Khoản 1.
    → KHÔNG CÓ CON ĐƯỜNG NÀO ĐI THẲNG LÊN KHOẢN 2, DÙ tiền án đó có đặc điểm "tái phạm nguy hiểm" hay không.
    → BẮT BUỘC ánh xạ vào Khoản 1. KHÔNG ánh xạ vào Khoản 2.
  - Chỉ được xét Khoản 2 khi giá trị tài sản ĐÃ ĐỦ cấu thành tội phạm độc lập (≥ 2.000.000đ). Khi đó tiền án
    còn "tự do" và được phép kích hoạt tình tiết định khung tại Khoản 2.
  ★ Ví dụ: trộm cắp 476.000đ + có tiền án 05/2022 chưa xóa → tiền án tiêu hao ở Khoản 1 → ánh xạ Khoản 1, KHÔNG Khoản 2.

- SAU KHI đã xác nhận giá trị ≥ ngưỡng (tiền án còn tự do): NẾU sự kiện có chứa các tình tiết tăng nặng
  (như "tái phạm nguy hiểm", "có tổ chức", "chuyên nghiệp"...), HÃY ĐỌC KỸ từng khoản của điều luật tội danh.
- Nếu khoản cao hơn (Khoản 2, 3...) có CHÍNH THỨC quy định tình tiết đó, BẮT BUỘC phải ánh xạ vào khoản cao hơn đó.
- Nếu điều luật KHÔNG quy định tình tiết đó làm dấu hiệu định khung, thì giữ nguyên ở khoản cơ bản (thường là Khoản 1) và tình tiết đó chỉ là tình tiết tăng nặng chung.


NGUYÊN TẮC THỜI HIỆU (Điều 7 BLHS) — BẮT BUỘC ÁP DỤNG:
1. QUY TẮC CƠ BẢN: Áp dụng luật có hiệu lực tại THỜI ĐIỂM PHẠM TỘI (tài liệu có role=primary).
2. NGOẠI LỆ HỒI TỐ CÓ LỢI: Nếu luật MỚI HƠN (role=comparison) quy định hình phạt NHẸ HƠN, BẮT BUỘC áp dụng.
3. NGHIÊM CẤM hồi tố nếu luật mới NẶNG HƠN — giữ luật cũ.
4. ĐA TỘI DANH: So sánh từng tội danh riêng biệt.
5. ĐA BỊ CÁO: Mỗi bị cáo xét theo ngày họ thực hiện hành vi.

Trả về JSON array:
[
  {
    "article": "Điều 168",
    "clause": "Khoản 2",
    "offense_name": "Tội cướp tài sản",
    "applicable_reason": "Lý do áp dụng điều này",
    "edition_applied": "BLHS 2015 (sửa đổi 2017)",
    "edition_reason": "Nếu KHÔNG có tài liệu comparison: 'Áp dụng luật có hiệu lực tại thời điểm phạm tội'. Nếu CÓ tài liệu comparison: 'Áp dụng luật tại thời điểm phạm tội do luật mới không có lợi hơn'."
  }
]
OUTPUT: CHỈ JSON array hợp lệ."""

        try:
            response = llm.invoke(_sanitize_msgs([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"SỰ KIỆN:\n{facts_str}\n\nVĂN BẢN LUẬT (có nhãn role):\n{context}\n\nVỤ ÁN:\n{case_text}")
            ]))
            raw_output = response.content
            print(f"  [MAP_LAWS] LLM output:\n{raw_output}")

            mapped = _extract_json(raw_output)
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
