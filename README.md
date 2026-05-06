# VNPLaw — Vietnamese Penal Law Chatbot

Hệ thống tư vấn pháp lý hình sự thông minh dựa trên AI, RAG, và LangGraph.

---

## Architecture

```
React Frontend (Vite + React Router)
       ↓  REST API via nginx proxy (/api, /ai-api)
Spring Boot Backend (JWT Auth, Chat Sessions, History, Admin)
       ↓  Internal HTTP (WebFlux WebClient)
Python FastAPI AI Service (RAG + LangGraph + Gemini 2.5 Flash)
       ↓
Milvus Lite (local .db, semantic vector search)  +  PostgreSQL (relational data)
       ↓
OpenRouter API → google/gemini-2.5-flash
```

---

## Features

**Part A — Core Case-Resolution**
- Analyzes criminal cases from three legal perspectives: Judge (Thẩm phán), Defense Lawyer for the accused (Luật sư Bào chữa), Defense Lawyer for the victim (Luật sư Bị hại)
- Returns structured legal reasoning: applicable articles (Bộ luật Hình sự), key facts, role-specific argument direction
- Law sidebar: click any cited article to read the full text from PostgreSQL, with automatic version selection based on crime date
- Practice Mode: submit your own legal analysis and get an AI score (0–100) with detailed structured feedback

**Part B — Administration & Quality Monitoring**
- Dashboard with aggregate statistics: total sessions, case count, breakdown by role and province
- Unique daily visitor tracking via client-side UUID (localStorage) + DB-level deduplication
- Users submit Correct/Incorrect feedback on AI responses with optional comment
- Admin can view all feedback with full conversation context and update review status
- Per-user case statistics for admin monitoring

**Core Infrastructure**
- Optional authentication — chat as guest or register for persistent history
- Guest sessions identified by browser-generated `guestId` (localStorage)
- Authenticated sessions saved to PostgreSQL (accessible from any device)
- JWT-based security via Spring Security

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite 6, React Router v7, React Markdown, Axios, CSS Modules |
| Backend | Java 21, Spring Boot 3.4.5, Spring Security (JWT, jjwt 0.12.6), Spring Data JPA, WebFlux (WebClient), Spring Cache, PostgreSQL |
| AI Service | Python 3.10+, FastAPI 0.111, Uvicorn 0.30, LangGraph, LangChain, langchain-openai |
| Embedding | `trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05` (LoRA fine-tuned Jina v5 Nano, via PEFT + SentenceTransformers) |
| Reranker | `BAAI/bge-reranker-v2-m3` — multilingual cross-encoder, 8192-token context |
| LLM | `google/gemini-2.5-flash` via OpenRouter (1M token context, temp=0) |
| Vector DB | Milvus Lite (local `.db` file — no separate server required) |
| Relational DB | PostgreSQL 16 |
| Serving | nginx (static frontend + reverse proxy for `/api` and `/ai-api`) |
| Deployment | Bare-metal (Ubuntu 22.04) via `deploy_nodocker.sh` |

---

## Project Structure

```
PenalLawChatbot/
├── ai-service/
│   ├── app/
│   │   └── main.py                # LangGraph pipeline: fact extraction, law mapping,
│   │                               # sentencing calculation, rebuttal, practice eval
│   ├── scripts/                   # Data ingestion, embedding, dataset analysis
│   ├── VN_law_lora.db             # Pre-built Milvus Lite vector DB
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── backend/
│   └── src/main/java/com/penallaw/backend/
│       ├── controller/            # AuthController, ChatController, LawController,
│       │                           # StatsController, AdminController
│       ├── service/               # AuthService, ChatService, AdminService,
│       │                           # VisitorTrackingService
│       ├── entity/                # User, ChatSession, ChatMessage, Law,
│       │                           # Feedback, DailyVisit, SiteStats
│       ├── repository/            # JPA repositories
│       ├── security/              # JwtService, JwtAuthenticationFilter
│       ├── config/                # SecurityConfig, CacheConfig
│       ├── client/                # AiServiceClient (WebFlux WebClient)
│       ├── converter/             # JsonListConverter (JPA attribute converter)
│       ├── dto/                   # AuthDTOs, ChatDTOs, AdminDTOs, LawDTOs
│       └── exception/             # GlobalExceptionHandler
├── frontend/
│   └── src/
│       ├── pages/                 # ChatPage, TrainingPage, LoginPage,
│       │                           # RegisterPage, AdminPage, StatsPage
│       ├── components/            # UI components (RoleSelector, MessageBubble, etc.)
│       ├── services/
│       │   └── api.js             # Axios client: authApi, chatApi, lawsApi,
│       │                           # adminApi, practiceApi, trackVisitApi
│       └── hooks/
│           └── useAuth.jsx        # Auth context + JWT management
├── database/
│   └── migrations/                # SQL migration scripts
├── scripts/
│   ├── setup_server.sh            # Initial server setup (Ubuntu 22.04)
│   ├── deploy_nodocker.sh         # Bare-metal full deployment (all services)
│   ├── restore_database.sh        # PostgreSQL restore from backup
│   ├── backup_database.sh         # PostgreSQL backup
│   ├── check_db_status.sh         # Database health check
│   └── merge_backups.py           # Merge partial backup files
├── docs/                          # Tutorials, troubleshooting guides
├── docker-compose.yml
└── .env.example
```

---

## Environment Variables

Copy `.env.example` to `.env` on the server and fill in exactly two values:

```bash
cp .env.example .env
```

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OPENROUTER_API_KEY` | — | Yes | API key from openrouter.ai |
| `HF_TOKEN` | — | Yes | HuggingFace token (to download LoRA adapter) |
| `JWT_SECRET` | (pre-filled) | Yes | Secret for signing JWTs |
| `POSTGRES_DB` | `penallaw` | No | PostgreSQL database name |
| `POSTGRES_USER` | `postgres` | No | PostgreSQL username |
| `POSTGRES_PASSWORD` | `postgres` | No | PostgreSQL password |

AI Service config (`COLLECTION_NAME`, `LLM_MODEL`, `TOP_K`, `FORCE_CPU`, `EMBEDDING_ADAPTER`) is set directly in `ai-service/app/main.py` — no `.env` override needed unless you want to change models.

---

## Deployment (Bare-Metal, No Docker)

The production deployment runs four services directly on the host (Ubuntu 22.04):

| Service | Port | Process |
|---------|------|---------|
| PostgreSQL | 5432 | `pg_ctlcluster` |
| AI Service | 8000 | `uvicorn` (Python 3.10) |
| Spring Boot Backend | 8080 | `java -jar` (Java 21) |
| Frontend | 80 | `nginx` (static files) |

**First-time setup:**
```bash
# 1. Provision the server (installs Java 21, Python 3.10, Node.js 20, nginx, PostgreSQL)
bash scripts/setup_server.sh

# 2. Restore the database backup
bash scripts/restore_database.sh

# 3. Deploy all services
bash scripts/deploy_nodocker.sh
```

**Re-deploy after code changes:**
```bash
bash scripts/deploy_nodocker.sh
```

The deploy script is idempotent — it skips `npm install` / `mvn build` / PostgreSQL startup if nothing has changed.

**Process persistence (prevent services stopping when SSH closes):**
```bash
# Use tmux to keep processes running after disconnect
tmux new -s deploy
bash scripts/deploy_nodocker.sh
# Press Ctrl+B then D to detach. Services stay running.
```

**Log locations:**
```
/var/log/penallaw/ai-service.log
/var/log/penallaw/backend.log
/var/log/nginx/error.log
```

**Manual restart commands:**
```bash
# AI Service
pkill -f uvicorn; cd /root/PenalLawChatbot/ai-service && \
  nohup /usr/bin/python3.10 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  >> /var/log/penallaw/ai-service.log 2>&1 &

# Backend
pkill -f java; nohup java -jar /root/PenalLawChatbot/backend/target/*.jar \
  --server.port=8080 >> /var/log/penallaw/backend.log 2>&1 &

# nginx
pkill nginx; nginx
```

---

## API Reference

### Authentication (`/api/auth`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/register` | None | Register new user |
| POST | `/api/auth/login` | None | Login, returns JWT token |

### Dashboard & Visitor Tracking (`/api/home`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/home` | None | Aggregate system statistics |
| POST | `/api/home/track-visit` | None | Record a unique daily visit (body: `{ "visitor_id": "<uuid>" }`) |

### Chat — Guest Sessions (`/api/chat/guest`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/chat/guest/{guestId}/sessions` | None | Create a guest session |
| GET | `/api/chat/guest/{guestId}/sessions` | None | List guest sessions |

### Chat — Authenticated Sessions (`/api/chat`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/chat/sessions` | JWT | Create a new session |
| GET | `/api/chat/sessions` | JWT | List user's sessions |

### Chat — Messages (guest & authenticated)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/chat/sessions/{sessionId}/messages` | None | Send message → triggers AI pipeline |
| GET | `/api/chat/sessions/{sessionId}/messages` | None | Get conversation history |
| DELETE | `/api/chat/sessions/{sessionId}` | None | Delete a session |

**`POST /api/chat/sessions/{sessionId}/messages` request body:**
```json
{
  "content": "Bị cáo A dùng dao...",
  "role": "defense | victim | neutral",
  "rebuttal_against": "optional — the opposing argument text to rebut"
}
```

### Law Lookup (`/api/laws`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/laws/{articleNumber}` | None | Fetch full article text from PostgreSQL |

Query params: `crimeDate=YYYY-MM-DD` (selects historically applicable version), `source=Bộ luật Hình sự 2025` (exact source filter).

### Admin (`/api/admin`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/admin/stats` | ROLE_ADMIN | Admin dashboard statistics |
| GET | `/api/admin/feedback` | ROLE_ADMIN | All feedback with full session context |
| POST | `/api/admin/feedback` | None | Submit feedback on an AI response |
| PATCH | `/api/admin/feedback/{id}/status` | ROLE_ADMIN | Update feedback review status |
| GET | `/api/admin/user-stats` | ROLE_ADMIN | Per-user case counts |

**`POST /api/admin/feedback` request body:**
```json
{
  "session_id": "<uuid>",
  "message_id": "<uuid>",
  "is_correct": true,
  "comment": "optional explanation"
}
```

### AI Service (internal + proxied via `/ai-api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/predict` | Full legal analysis (RAG + LangGraph pipeline) |
| POST | `/practice/evaluate` | Score & feedback for user's own legal analysis |
| GET | `/health` | Health check + device status (CPU/GPU) |

---

## LangGraph Pipeline (`/predict`)

The pipeline classifies intent on every request and routes accordingly:

```
START
  └─ classify_intent ──► casual      → casual_respond       → END
                    ──► followup     → followup_generate     → END
                    ──► new_case     → clarification_check
                                          ↓ (fields missing?)
                                     rewrite → retrieve → grade_documents
                                          ↓ (relevant docs found?)
                                     extract_facts → map_laws → generate → END
                                                                    ↓ (rebuttal mode?)
                                                               rebuttal → END
```

**Pipeline nodes:**

| Node | Role |
|------|------|
| `classify_intent` | Routes: `casual` / `followup` / `new_case` |
| `clarification_check` | Checks for required fields (crime act, crime date) |
| `rewrite` | Generates 3 role-specific retrieval queries from the case |
| `retrieve` | Tri-Path semantic search in Milvus Lite (TOP_K=15 candidates) + BGE-M3 reranking |
| `grade_documents` | LLM-based relevance filter on retrieved law articles |
| `extract_facts` | Structured JSON extraction: crime act, date, defendants, aggravating/mitigating factors |
| `map_laws` | Maps facts to specific BLHS articles with edition-aware lookup (BLHS 1999/2009/2017/2025) |
| `generate` | Role-specific legal argument (defense / victim / neutral judge) |
| `rebuttal` | Counter-argument against an opposing argument (`rebuttal_against`) |
| `followup_generate` | Contextual follow-up using session history |
| `casual_respond` | Handles greetings and off-topic messages |

**Legal code edition routing** — the pipeline automatically selects the correct Bộ luật Hình sự edition based on the crime date:

| Crime Date Range | Applied Edition |
|-----------------|----------------|
| 01/07/2000 – 31/12/2009 | BLHS 1999 |
| 01/01/2010 – 31/12/2017 | BLHS 1999 (sửa đổi 2009) |
| 01/01/2018 – 30/06/2025 | BLHS 2015 (sửa đổi 2017) |
| 01/07/2025 – present | BLHS 2015 (sửa đổi 2025) |

---

## Roles (Bias Modes)

| Role value | Vietnamese Label | Analysis Bias |
|------------|-----------------|---------------|
| `neutral` | Thẩm phán | Objective, balanced judgment |
| `defense` | Luật sư Bào chữa | Minimize sentence, maximize mitigating factors (Điều 51 BLHS) |
| `victim` | Luật sư Bị hại | Maximize sentence, maximize civil compensation, cite aggravating factors (Điều 52 BLHS) |

---

## Practice Mode (`/practice/evaluate`)

Users write their own legal analysis for a given case, then submit it for AI scoring.

**Request:**
```json
{
  "case_description": "Bị cáo A dùng dao...",
  "user_mode": "defense | victim | neutral",
  "user_analysis": "Theo tôi, bị cáo phạm tội..."
}
```

**Response:**
```json
{
  "score": 75,
  "feedback": {
    "strengths": ["Xác định đúng tội danh theo Điều 134..."],
    "improvements": ["Chưa phân tích tình tiết giảm nhẹ Điều 51..."],
    "missed_articles": ["Điều 51 Bộ luật Hình sự 2015 (tình tiết giảm nhẹ)"],
    "suggestion": "Cần bổ sung phân tích về thời gian tạm giam..."
  }
}
```

---

## CPU Optimization

The AI service is optimized for CPU-only inference (no GPU required):

- PyTorch thread count pinned to all available physical cores via `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `torch.set_num_threads()`
- Auto-detection: uses GPU if CUDA is available, falls back to CPU with a warning
- Override with `FORCE_CPU=1` env var to force CPU regardless of GPU availability
- BGE-M3 reranker context of 8192 tokens handles the longest Vietnamese law articles (up to ~3,574 tokens for Điều 232 BLHS 2017) without truncation

---

## Database Entities

| Entity | Table | Description |
|--------|-------|-------------|
| `User` | `users` | Registered accounts (email, bcrypt password, role) |
| `ChatSession` | `chat_sessions` | Conversation containers (userId or guestId, role) |
| `ChatMessage` | `chat_messages` | Individual messages with AI response metadata |
| `Feedback` | `feedbacks` | User ratings on AI responses (isCorrect, comment, status) |
| `Law` | `laws` | Full article text from all BLHS editions |
| `DailyVisit` | `daily_visits` | Unique visitor tracking (visitor_id, visit_date, unique constraint) |
| `SiteStats` | `site_stats` | Aggregate counters |

---

## Development Mode (Local)

**AI Service:**
```bash
cd ai-service
cp .env.example .env   # fill in OPENROUTER_API_KEY and HF_TOKEN
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Backend:**
```bash
cd backend
# Set env vars: OPENROUTER_API_KEY, HF_TOKEN, JWT_SECRET, POSTGRES_* 
# or update src/main/resources/application.yml
mvn spring-boot:run
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev   # runs on http://localhost:5173
# Vite proxies: /api → http://localhost:8080, /ai-api → http://localhost:8000
```

---

## Deployment Scripts

| Script | Description |
|--------|-------------|
| `scripts/setup_server.sh` | Initial server provisioning (Java 21, Python 3.10, Node.js 20, nginx, PostgreSQL) |
| `scripts/deploy_nodocker.sh` | Full bare-metal deployment — builds and starts all four services |
| `scripts/restore_database.sh` | Restore PostgreSQL from a `.dump` backup file |
| `scripts/backup_database.sh` | Create a PostgreSQL backup |
| `scripts/check_db_status.sh` | Database connectivity and table health check |
| `scripts/merge_backups.py` | Merge split backup files into one |
| `scripts/deploy.sh` | Docker-based deployment (alternative) |
