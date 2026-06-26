# VNPLaw AI Microservice — Architecture Documentation
# (Reference Material for Thesis Chapter 4: AI Microservice Design)

---

## 1. Architectural Pattern: Layered Architecture (N-Tier)

The AI Microservice is built using a **Layered Architecture** (còn gọi là N-Tier Architecture). Đây là một trong những mẫu kiến trúc phần mềm lâu đời và phổ biến nhất trong kỹ nghệ phần mềm hiện đại.

### Nguyên tắc cốt lõi:
- Mỗi layer chỉ được phép giao tiếp với layer ngay bên dưới nó (unidirectional dependency).
- Không có layer nào import ngược lên layer phía trên (no upward dependency).
- Mỗi layer có một trách nhiệm duy nhất, rõ ràng (Single Responsibility).

### Cấu trúc thư mục ánh xạ vào các layer:

```
ai-service/app/
├── main.py             ← Layer 1: Presentation Layer (API Gateway)
├── core/               ← Layer 2: Configuration & Contract Layer
│   ├── config.py
│   └── schemas.py
├── services/           ← Layer 3: Infrastructure / Service Layer
│   ├── container.py
│   ├── embeddings.py
│   └── reranker.py
├── utils/              ← Layer 4: Utility / Helper Layer
│   ├── text.py
│   ├── legal.py
│   ├── dates.py
│   └── sentencing.py
└── graph/              ← Layer 5: Business Logic Layer (AI Pipeline)
    └── (see Chapter 5 — LangGraph Workflow Design)
```

---

## 2. Layer-by-Layer Description

---

### Layer 1 — Presentation Layer: `main.py`

**Role:** Đây là điểm vào duy nhất (entry point) của toàn bộ AI Microservice. File này hoạt động như một API Gateway, chịu trách nhiệm:

1. **HTTP Request Handling:** Nhận các yêu cầu HTTP từ Spring Boot backend thông qua FastAPI framework.
2. **Input Validation:** Validate payload của request bằng Pydantic models (`RequestBody`, `PracticeEvalRequest`) trước khi xử lý.
3. **Application Lifecycle Management:** Quản lý vòng đời ứng dụng qua `lifespan()` context manager — khởi tạo toàn bộ AI models khi server boot, giải phóng tài nguyên khi shutdown.
4. **Dependency Injection (DI):** Tại startup, khởi tạo tất cả các heavy dependencies (LLM, Embedding model, Reranker, Milvus, BM25 index) và inject chúng vào LangGraph qua `build_graph()`.
5. **HTTP Response Serialization:** Đóng gói kết quả từ LangGraph thành response JSON chuẩn.

**API Endpoints được định nghĩa trong `main.py`:**

| Endpoint | Method | Mô tả |
|---|---|---|
| `/health` | GET | Health check — trả về trạng thái model loading, thiết bị (CPU/GPU) |
| `/predict` | POST | Endpoint chính — nhận hồ sơ vụ án, trả về phân tích pháp lý |
| `/practice/evaluate` | POST | Practice Mode — nhận phân tích của user, trả về điểm và feedback |

**Startup Flow trong `lifespan()`:**
```
1. Login HuggingFace (tải fine-tuned models)
2. Load Jina Embedding model (GPU/CPU auto-detect)
3. Connect to Milvus Lite vector database
4. Initialize ChatOpenAI LLM (via OpenRouter)
5. Load BGE-Reranker-v2-m3 cross-encoder
6. Build BM25 keyword index (load toàn bộ corpus từ Milvus vào RAM)
7. Call build_graph() → compile LangGraph state machine
8. Store compiled graph in _svc.graph singleton
```

**Kỹ thuật đặc biệt trong `main.py`:**
- **CPU Thread Pinning:** Đặt `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` và `torch.set_num_threads()` TRƯỚC khi import torch để tối ưu hóa hiệu suất CPU.
- **pkg_resources Shim:** Inject một stub module vào `sys.modules` để fix lỗi import của `milvus-lite < 2.4.9` trong môi trường conda.
- **MILVUS_URI Pop:** `os.environ.pop("MILVUS_URI", None)` ngăn pymilvus đọc nhầm biến môi trường và crash với lỗi "Illegal uri".

---

### Layer 2 — Configuration & Contract Layer: `core/`

Layer này cung cấp hai thứ cho toàn bộ hệ thống: **cấu hình** và **hợp đồng dữ liệu**.

---

#### `core/config.py`

**Role:** Single source of truth cho toàn bộ cấu hình của AI service. Tất cả constants và environment variables được đọc tại đây và import từ đây — không có file nào khác được phép đọc `os.getenv()` trực tiếp ngoại trừ `main.py`.

**Nội dung chính:**
- `MILVUS_URI`: Đường dẫn file Milvus Lite database (`.db` file).
- `COLLECTION_NAME`: Tên collection trong Milvus chứa các điều luật BLHS.
- `TOP_K`: Số lượng documents tối đa trả về từ mỗi semantic search query.
- `LLM_MODEL`: Tên model LLM sử dụng (default: `google/gemini-2.5-flash` qua OpenRouter).
- `_DEFAULT_EMBEDDING`: Tên model embedding mặc định (Jina v5 Nano fine-tuned trên dữ liệu pháp lý Việt Nam).
- `OUTPUT_FIELDS`: Danh sách các trường dữ liệu cần lấy từ Milvus cho mỗi document.
- `DEVICE`: Kết quả auto-detect thiết bị tính toán (CPU hoặc `cuda:N`).
- `_EDITION_RANGES`: Bảng tra cứu ánh xạ ngày phạm tội → tên ấn bản BLHS áp dụng.

**Hàm `_detect_device()`:**
Logic tự động phát hiện GPU. Chiến lược phân phối GPU cho multi-worker deployment:
```
gpu_index = os.getpid() % gpu_count
```
Khi uvicorn chạy với `--workers N`, mỗi worker process có PID khác nhau, công thức trên đảm bảo mỗi worker tự động chiếm một GPU khác nhau theo round-robin.

---

#### `core/schemas.py`

**Role:** Định nghĩa **hợp đồng dữ liệu** (`AgentState`) được dùng xuyên suốt toàn bộ LangGraph pipeline. Đây là `TypedDict` trung tâm — mọi node trong graph đều đọc và ghi vào cấu trúc này.

**Các trường của `AgentState`:**

| Trường | Kiểu | Mô tả |
|---|---|---|
| `messages` | `Sequence[BaseMessage]` | Lịch sử hội thoại LangChain (auto-append via `add_messages`) |
| `question` | `str` | Nội dung tin nhắn hiện tại của user |
| `full_case_content` | `str` | Toàn bộ nội dung hồ sơ vụ án gốc |
| `documents` | `List[Document]` | Danh sách điều luật đã được retrieve và rerank |
| `retrieval_queries` | `List[str]` | 3 queries được tạo bởi `multi_query_rewrite_node` |
| `user_role` | `Literal["defense","victim","neutral"]` | Vai trò phân tích |
| `extracted_facts` | `Dict` | Các sự kiện pháp lý được LLM extract từ hồ sơ |
| `mapped_laws` | `List[Dict]` | Kết quả ánh xạ điều luật (tội danh, điều khoản, ấn bản BLHS) |
| `sentencing_data` | `Dict` | Dữ liệu lượng hình tính toán xác định (tuổi, thời gian tạm giam) |
| `chat_history` | `List[Dict]` | Lịch sử hội thoại từ frontend |
| `_missing_fields` | `List[str]` | Danh sách trường còn thiếu (set bởi `clarification_check_node`) |
| `per_defendant_dates` | `List[Dict]` | Ngày phạm tội của từng bị cáo (multi-defendant support) |
| `is_practice_mode` | `bool` | Flag kích hoạt Practice Mode |
| `user_analysis` | `str` | Bài phân tích của user trong Practice Mode |

---

### Layer 3 — Infrastructure / Service Layer: `services/`

Layer này đóng gói toàn bộ tương tác với các hệ thống bên ngoài (AI models, databases). Đây là nơi áp dụng **Dependency Injection (DI) Container pattern**.

---

#### `services/container.py`

**Role:** Định nghĩa class `Services` — một **DI Container** lưu trữ toàn bộ các singleton dependencies.

**Lý do cần DI Container:**
AI models như BGE-Reranker-v2-m3 chiếm ~1.3GB VRAM và mất 30–60 giây để load. DI Container đảm bảo:
- Mỗi dependency được khởi tạo **đúng một lần** tại startup.
- Tất cả API routes dùng chung **một instance duy nhất** thông qua `_svc`.
- Khi test, có thể mock bất kỳ dependency nào bằng cách thay thế field trong `_svc`.

**Các fields trong `Services`:**

| Field | Kiểu | Mô tả |
|---|---|---|
| `llm` | `ChatOpenAI` | LLM instance kết nối OpenRouter |
| `retriever` | `MilvusRetriever` | Thin wrapper thực hiện dense ANN search trên Milvus |
| `milvus_client` | `MilvusClient` | Raw Milvus client (dùng cho pinned-article lookup) |
| `reranker_fn` | `Callable` | Hàm scoring cross-encoder, trả về raw logits |
| `bm25_index` | `BM25Okapi` | BM25 keyword index (in-memory, built từ toàn bộ corpus) |
| `bm25_docs` | `List[Document]` | Corpus tương ứng với BM25 index |
| `graph` | `CompiledGraph` | LangGraph compiled state machine sau khi `build_graph()` |
| `model_loaded` | `bool` | Sentinel flag cho `/health` endpoint |

---

#### `services/embeddings.py`

**Role:** Chứa hai class phục vụ vector retrieval:

**Class `JinaEmbeddings`:**
- LangChain-compatible embedding wrapper cho Jina v5 Nano model.
- Hỗ trợ auto-detect xem model có hỗ trợ `task=` kwarg không (base Jina hỗ trợ, LoRA adapter không hỗ trợ).
- `batch_size` điều chỉnh tự động: 64 trên GPU, 32 trên CPU.

**Class `MilvusRetriever`:**
- Thin wrapper thực hiện Approximate Nearest Neighbor (ANN) search trên Milvus Lite.
- Dùng **COSINE similarity metric**.
- Hỗ trợ `top_k_override` per-call: Q1 dùng `k=20`, Q2/Q3 dùng `k=10`.
- Trả về `langchain_core.documents.Document` với đầy đủ metadata.

---

#### `services/reranker.py`

**Role:** Cung cấp cross-encoder reranking.

**Hàm `load_reranker(model_name, device)`:**
- Load `BAAI/bge-reranker-v2-m3` thông qua `AutoModelForSequenceClassification` và `AutoTokenizer`.
- **Lý do dùng AutoModel trực tiếp thay vì `sentence_transformers.CrossEncoder`:** `sentence_transformers >= 4.57` thay đổi `BatchEncoding` API gây crash khi dùng `CrossEncoder`. AutoModel trực tiếp hoạt động ổn định trên mọi phiên bản `transformers`.
- Trên GPU: dùng `torch.float16` (fp16); CPU: `torch.float32` (fp32).

**Hàm `make_rerank_scorer(tokenizer, model, device)`:**
- Factory function tạo ra callable `_rerank_scores(pairs, batch_size) → List[float]`.
- Input: danh sách tuple `(query, doc_text)`. Output: raw logits.
- Nếu model load thất bại: trả về hàm noop — service vẫn hoạt động, chỉ không rerank.
- Batch processing 8 pairs để tránh OOM.

---

### Layer 4 — Utility / Helper Layer: `utils/`

Chứa các hàm thuần túy (pure functions) — không side effects, không external dependencies ngoại trừ `core/config.py`.

---

#### `utils/text.py`

**Hàm `sanitize_text(text)`:**
- Loại bỏ các ký tự surrogate (U+D800–U+DFFF) xuất hiện khi scrape PDF tiếng Việt.
- Dùng `text.encode("utf-8", "replace").decode("utf-8")`.
- **Tại sao quan trọng:** Surrogate characters làm crash Python's JSON encoder với `UnicodeEncodeError`.

**Hàm `_sanitize_msgs(messages)`:**
- Sanitize toàn bộ content của LangChain messages ngay trước `llm.invoke()`.
- Trả về NEW message objects — không mutate originals để tránh corrupt `chat_history`.

**Hàm `cleanup_response(text)`:**
- Thay `"BLHS"` → `"Bộ luật Hình sự"`, giữ nguyên `"BLHS 1999"`, `"BLHS 2015 (sửa đổi 2017)"`.
- Thu gọn markdown table separator dashes quá dài do LLM sinh ra.

---

#### `utils/legal.py`

**Hàm `_extract_json(text)`:**
- Trích xuất JSON từ output LLM theo chiến lược 3 bước: Strip fences → Find brackets → `json.loads()`.
- Xử lý: conversational preamble, trailing commentary, partial fences.
- Hỗ trợ cả JSON object (`{...}`) và JSON array (`[...]`).

---

#### `utils/dates.py`

**Hàm `_edition_for_date(date_str)`:**
- Tra cứu bảng `_EDITION_RANGES` trong `config.py` → trả về tên ấn bản BLHS tương ứng với ngày phạm tội.
- Cơ sở của **nguyên tắc hiệu lực pháp luật theo thời gian (Điều 7 BLHS)**.

---

#### `utils/sentencing.py`

**Role:** Tính toán xác định các dữ liệu lượng hình để inject vào LLM prompt.

| Hàm | Đầu vào | Đầu ra | Mô tả |
|---|---|---|---|
| `parse_date(text)` | Chuỗi ngày | `datetime` hoặc `None` | Parser đa định dạng |
| `compute_detention_months(arrest, trial)` | 2 chuỗi ngày | `float` | Số tháng tạm giam |
| `compute_age_at_crime(dob, crime_date)` | 2 chuỗi ngày | `float` | Tuổi tại thời điểm phạm tội |
| `extract_sentencing_data(facts)` | `Dict` facts | `Dict` | Orchestrator gọi các hàm trên |

**Lý do tính toán xác định thay vì để LLM tự tính:**
LLM thường tính sai các phép tính ngày tháng. Bằng cách tính trước trong Python và inject kết quả vào prompt với lệnh "BẮT BUỘC SỬ DỤNG", ta loại bỏ hoàn toàn nguồn lỗi này.

---

## 3. Dependency Injection Flow

```
                    SERVER STARTUP (lifespan())
                           │
          ┌────────────────┼─────────────────────────┐
          ▼                ▼                         ▼
    JinaEmbeddings    MilvusClient           load_reranker()
    (embeddings.py)   (pymilvus)             (reranker.py)
          │                │                         │
          └───────┬─────────┘                        │
                  ▼                                  │
           MilvusRetriever            BM25Okapi      │
           (embeddings.py)          (rank_bm25)      │
                  │                      │           │
                  └──────────┬───────────┘           │
                             ▼                       │
                       build_graph(                  │
                           llm,          ◄───────────┘
                           retriever,
                           milvus_client,
                           reranker_fn,
                           bm25_index,
                           bm25_docs
                       )
                             │
                             ▼
                     compiled LangGraph
                             │
                             ▼
                      _svc.graph = graph   ← stored in global singleton
                             │
                    ─────────┴───────────
                   │                     │
         POST /predict          POST /practice/evaluate
                   │                     │
         _svc.graph.ainvoke()   _svc.graph.ainvoke()
```

---

## 4. Key Design Decisions & Rationale

### 4.1 Tại sao dùng Layered Architecture?

Trước khi refactor, toàn bộ service là một file `main.py` monolithic dài 3298 dòng. Kiến trúc phân layer giải quyết:

| Vấn đề (monolithic) | Giải pháp (layered) |
|---|---|
| Khó đọc, khó debug | Mỗi file có một trách nhiệm rõ ràng |
| Không thể test riêng từng component | Mỗi utility function là pure function, test độc lập |
| Thay đổi model embedding → phải sửa nhiều chỗ | Chỉ sửa `services/embeddings.py` |
| Thêm endpoint mới → risk breaking existing logic | Chỉ thêm route trong `main.py` |

### 4.2 Tại sao dùng `dataclass` cho DI Container thay vì global variables?

Global variables (`model = None`) có nhược điểm:
- Không có type hints → IDE không hỗ trợ autocomplete.
- Khó mock trong unit test.
- Không thể enforce required fields.

`@dataclass class Services` giải quyết tất cả: type-safe, mockable, declarative.

### 4.3 Tại sao không dùng FastAPI's `Depends()`?

FastAPI `Depends()` chạy per-request, không phải per-startup. AI models quá nặng để khởi tạo per-request. Module-level singleton (`_svc`) kiểm soát lifecycle tốt hơn cho heavy AI resources.

---

## 5. Summary Table: All Files & Their Roles

| File | Layer | Role |
|---|---|---|
| `main.py` | Presentation | HTTP API Gateway, startup lifecycle, DI orchestration, routing |
| `core/config.py` | Configuration | Environment variables, model constants, device detection, BLHS edition ranges |
| `core/schemas.py` | Configuration | `AgentState` TypedDict — shared data contract for the entire LangGraph pipeline |
| `services/container.py` | Infrastructure | `Services` dataclass — DI Container holding all singleton AI dependencies |
| `services/embeddings.py` | Infrastructure | `JinaEmbeddings` (vector encoder) + `MilvusRetriever` (ANN search) |
| `services/reranker.py` | Infrastructure | `load_reranker()` (model loading) + `make_rerank_scorer()` (scoring factory) |
| `utils/text.py` | Utility | Text sanitization, surrogate removal, LLM response cleanup |
| `utils/legal.py` | Utility | Robust JSON extraction from LLM output |
| `utils/dates.py` | Utility | Date parsing + BLHS edition lookup by crime date |
| `utils/sentencing.py` | Utility | Deterministic sentencing calculations (age at crime, detention months) |
| `graph/` | Business Logic | LangGraph AI pipeline (see Chapter 5) |
