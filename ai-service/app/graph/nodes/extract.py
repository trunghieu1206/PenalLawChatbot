"""
graph/nodes/extract.py — VNPLaw AI Service
Node 1: extract_facts_node
Node 2.5: clarification_check_node, clarification_node, clarification_router
"""

import json
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.config import REQUIRED_FIELDS, _MIN_SUPPORTED_DATE, _VN_TZ
from app.core.schemas import AgentState
from app.utils.legal import _extract_json
from app.utils.text import _sanitize_msgs
from app.utils.sentencing import extract_sentencing_data


def make_extract_nodes(llm, measure_time):
    """Factory: returns the three extraction-related node callables bound to 'llm'."""

    @measure_time('extract_facts')
    def extract_facts_node(state: AgentState) -> dict:
        """Extract structured legal facts from case text."""
        print("[NODE: extract_facts]")
        case_text = state.get("full_case_content", state["question"])

        system_prompt = """Bạn là chuyên gia phân tích hồ sơ pháp lý.
Nhiệm vụ: Đọc kỹ nội dung vụ án và trích xuất thông tin có cấu trúc.
Trả về JSON với các trường sau (dùng null nếu không tìm thấy thông tin):
{
  "hanh_vi": "mô tả hành vi phạm tội",
  "hau_qua": "hậu quả gây ra",
  "dong_co": "động cơ",
  "doi_tuong": "đối tượng bị hại",
  "cong_cu": "công cụ phương tiện",
  "tinh_tiet_tang_nang": ["list tình tiết tăng nặng"],
  "tinh_tiet_giam_nhe": ["list tình tiết giảm nhẹ"],
  "ngay_pham_toi": "dd/mm/yyyy",
  "ngay_xet_xu": "dd/mm/yyyy nếu có trong mô tả, nếu không để null",
  "loi_pham_toi": "cố ý trực tiếp | cố ý gián tiếp | vô ý quá tự tin | vô ý cẩu thả | null",
  "giai_doan_pham_toi": "hoàn thành | chưa đạt | chuẩn bị | tự ý chấm dứt | null",
  "vai_tro_dong_pham": "thực hành | tổ chức | xúi giục | giúp sức | null nếu phạm tội một mình",
  "co_dau_hieu_loai_tru_tnhs": "mô tả ngắn gọn dấu hiệu loại trừ TNHS (phòng vệ chính đáng/tình thế cấp thiết/không có NLTNHS/sự kiện bất ngờ) nếu có, null nếu không",
  "ngay_sinh_nan_nhan": "dd/mm/yyyy",
  "ngay_sinh_bi_cao": "dd/mm/yyyy",
  "ngay_tam_giam": "dd/mm/yyyy",
  "ten_bi_cao": "tên bị cáo (nếu có nhiều bị cáo, để dạng 'A, B, C')",
  "co_tien_an": "true/false (⚠️ LƯỤ Ý QUAN TRỌNG: Chỉ set true nếu văn bản ghi rõ là CÓ tiền án / chưa xóa án tích. Nếu văn bản ghi 'Tiền án: Không' và các bản án cũ chỉ nằm ở mục 'Nhân thân' hoặc 'đã được xóa án tích', BẮT BUỘC set là false)",
  "da_boi_thuong": true/false,
  "da_thanh_khan_khai_bao": true/false,
  "is_multi_defendant": true/false,
  "so_luong_bi_cao": integer or null,
  "tang_vat_loai": "loại tang vật",
  "tang_vat_so_luong": "số lượng / khối lượng",
  "dia_danh": "tỉnh / thành phố nơi xảy ra vụ án (ví dụ: 'Hà Nội', 'Bình Thuận', 'TP. Hồ Chí Minh') — chỉ tên tỉnh/thành, null nếu không có",
  "per_defendant_dates": [
    {"name": "tên bị cáo", "ngay_pham_toi": "dd/mm/yyyy"}
  ]
}

QUY TẮc TRÍCH XUẤT per_defendant_dates:
- Chỉ điền nếu is_multi_defendant = true VÀ mỗi bị cáo có ngày phạm tội riêng trong mô tả.
- Nếu một bị cáo không có ngày riêng → dùng ngày chung từ "ngay_pham_toi".
- Nếu chỉ có một bị cáo hoặc không xác định được → để null (không phải []).
- CHỈ trích xuất thông tin CÓ TRONG mô tả. TUYỆT ĐỐI KHÔNG bọa đặt thông tin.

LƯỤ Ý: Trích xuất "ngay_xet_xu" nếu có trong mô tả (ví dụ: ngày tòa xét xử, ngày phiên tòa).
Nếu không tìm thấy, trả về null — hệ thống sẽ tự động dùng ngày hiện tại.
OUTPUT: CHỈ xuất JSON hợp lệ, không markdown, không giải thích."""

        try:
            response = llm.invoke(_sanitize_msgs([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"NỘI DUNG VỤ ÁN:\n{case_text}")
            ]))
            facts = _extract_json(response.content)
        except Exception as e:
            print(f"⚠️  Fact extraction failed: {e}")
            facts = {}

        # ngay_xet_xu fallback
        if not facts.get("ngay_xet_xu"):
            if state.get("is_practice_mode"):
                facts["ngay_xet_xu"] = datetime.now(_VN_TZ).strftime("%d/%m/%Y")
                print(f"  ngay_xet_xu not in input (practice mode) — defaulted to today: {facts['ngay_xet_xu']}")
            else:
                facts["ngay_xet_xu"] = None
                print("  ngay_xet_xu not in input — left as None (no spurious retroactivity comparison)")
        else:
            print(f"  ngay_xet_xu extracted from input: {facts['ngay_xet_xu']}")

        per_defendant = facts.pop("per_defendant_dates", None) or None
        if per_defendant and not isinstance(per_defendant, list):
            per_defendant = None

        sentencing_data = extract_sentencing_data(facts)
        print("  ┌─── Extracted Facts (JSON) ───────────────────────────────────────")
        for k, v in facts.items():
            print(f"  │  {k}: {json.dumps(v, ensure_ascii=False)}")
        print(f"  └─── Sentencing data: {json.dumps(sentencing_data, ensure_ascii=False)}")

        return {
            "extracted_facts":     facts,
            "sentencing_data":     sentencing_data,
            "per_defendant_dates": per_defendant,
        }

    @measure_time('clarification_check')
    def clarification_check_node(state: AgentState) -> dict:
        """Validates MUST HAVE fields; writes _missing_fields to state."""
        print("[NODE: clarification_check]")
        facts = state.get("extracted_facts") or {}
        missing = [f for f in REQUIRED_FIELDS if not facts.get(f)]

        if not missing:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    crime_date = datetime.strptime(facts["ngay_pham_toi"].strip(), fmt).date()
                    if crime_date < _MIN_SUPPORTED_DATE:
                        missing.append("_date_out_of_range")
                    break
                except (ValueError, AttributeError):
                    continue

        if missing:
            print(f"  [CLARIFICATION CHECK] Missing fields: {missing}")
        else:
            print("  [CLARIFICATION CHECK] All required fields present — continuing")
        return {"_missing_fields": missing}

    def clarification_router(state: AgentState) -> str:
        route = "clarify" if state.get("_missing_fields") else "continue"
        mode = "practice" if state.get("is_practice_mode") else "consultation"
        print(f"  [ROUTER: clarification] mode={mode} → {route}")
        return route

    @measure_time('clarification')
    def clarification_node(state: AgentState) -> dict:
        print("[NODE: clarification]")
        is_practice = state.get("is_practice_mode", False)
        missing     = state.get("_missing_fields", [])

        if "_date_out_of_range" in missing:
            facts = state.get("extracted_facts") or {}
            date_str = facts.get("ngay_pham_toi", "?")
            if is_practice:
                payload = json.dumps({
                    "score": 0,
                    "feedback": {
                        "strengths": [],
                        "improvements": [
                            f"Ngày phạm tội không hợp lệ: {date_str}. "
                            "Chỉ hỗ trợ các vụ án từ ngày 01/07/2000 trở đi "
                            "(ngày BLHS 1999 có hiệu lực). Vui lòng kiểm tra lại."
                        ],
                        "suggestion": "Vui lòng cung cấp lại mô tả vụ án với ngày phạm tội hợp lệ.",
                        "suggested_laws": [],
                    },
                }, ensure_ascii=False)
                return {"messages": [AIMessage(content=payload)]}
            reply = (
                f"**Ngày phạm tội không hợp lệ:** `{date_str}`\n\n"
                "Chỉ hỗ trợ các vụ án có ngày phạm tội từ **01/07/2000** trở đi "
                "(ngày BLHS 1999 có hiệu lực).\n\n"
                "Vui lòng kiểm tra lại ngày phạm tội và gửi lại."
            )
            return {"messages": [AIMessage(content=reply)]}

        needed_labels = [REQUIRED_FIELDS[f] for f in missing if f in REQUIRED_FIELDS]

        if is_practice:
            missing_str = ", ".join(needed_labels)
            payload = json.dumps({
                "score": 0,
                "feedback": {
                    "strengths": [],
                    "improvements": [
                        f"Mô tả vụ án còn thiếu thông tin bắt buộc: {missing_str}. "
                        "Vui lòng bổ sung và gửi lại để hệ thống có thể chấm điểm chính xác."
                    ],
                    "suggestion": (
                        "Hãy đảm bảo mô tả vụ án bao gồm đầy đủ: "
                        "hành vi phạm tội cụ thể và ngày phạm tội."
                    ),
                    "suggested_laws": [],
                },
            }, ensure_ascii=False)
            return {"messages": [AIMessage(content=payload)]}

        reply = (
            "Để phân tích chính xác, Cần thêm thông tin sau:\n\n"
            + "\n".join(f"{i+1}. **{label}**" for i, label in enumerate(needed_labels))
            + "\n\nVui lòng bổ sung và gửi lại mô tả vụ án."
        )
        reply += (
            "\n\n**Thông tin tham khảo** (không bắt buộc, nhưng giúp phân tích tốt hơn):\n"
            "- Hậu quả gây ra (thương tích, thiệt hại tài sản)\n"
            "- Bị cáo có tiền án tiền sự không?\n"
            "- Bị cáo có thành khẩn khai báo / bồi thường không?\n"
            "- Tang vật thu giữ (loại, số lượng)"
        )
        return {"messages": [AIMessage(content=reply)]}

    return {
        "extract_facts":       extract_facts_node,
        "clarification_check": clarification_check_node,
        "clarification_router": clarification_router,
        "clarification":       clarification_node,
    }
