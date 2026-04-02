# ⚖️ LegalAI — Vietnamese Legal AI Chatbot

Hệ thống tư vấn pháp lý hình sự thông minh dựa trên AI, RAG, và LangGraph.

## Features

- 🤖 **AI-Powered Legal Analysis** — Analyzes criminal cases using RAG + LLM
- 👥 **Optional Authentication** — Use as guest or create account
- 💾 **Persistent Sessions** — Chat history saved to PostgreSQL for authenticated users
- 🛡️ **Role-Based Analysis** — Analyze from neutral, defense, or victim perspective
- 📊 **Law Extraction** — Automatically identifies relevant legal articles
- 🎓 **Training Mode** — Practice legal analysis with evaluation

## Architecture

```
React Frontend (Vite)
       ↓ REST API / nginx proxy
Spring Boot Backend (Auth, Chat Sessions, History)
       ↓ Internal HTTP (WebClient)
Python FastAPI AI Service (RAG + LangGraph + LLM)
       ↓
Milvus Lite (semantic search, local .db file) + PostgreSQL (storage)
       ↓
OpenRouter API (google/gemini-2.5-flash)
```

## Project Structure

```
PenalLawChatbot/
├── ai-service/                  # Python FastAPI AI service
│   ├── app/
│   │   └── main.py             # LangGraph pipeline (RAG, fact extraction, sentencing, practice eval)
│   ├── scripts/
│   │   ├── ingest_laws.py      # Data ingestion from JSON/DOCX to PostgreSQL
│   │   ├── embed_laws.py       # Batch embedding to Milvus (local)
│   │   ├── embed_laws_colab.py # Colab-compatible embedding script
│   │   └── parse_docx.py      # DOCX parser utility
│   ├── VN_law_lora.db          # Milvus Lite local vector DB (pre-built)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── backend/                     # Spring Boot API Gateway
│   └── src/main/java/com/penallaw/backend/
│       ├── controller/          # AuthController, ChatController
│       ├── service/             # AuthService, ChatService
│       ├── entity/              # User, ChatSession, ChatMessage
│       ├── repository/          # JPA repositories
│       ├── security/            # JwtService, JwtAuthenticationFilter
│       ├── config/              # SecurityConfig
│       ├── client/              # AiServiceClient (WebFlux WebClient)
│       ├── converter/           # JsonListConverter (JPA attribute converter)
│       ├── dto/                 # AuthDTOs, ChatDTOs
│       └── exception/           # GlobalExceptionHandler
├── frontend/                    # React + Vite
│   ├── src/
│   │   ├── pages/              # ChatPage, TrainingPage, LoginPage, RegisterPage
│   │   ├── components/         # RoleSelector, MessageBubble
│   │   ├── services/           # api.js (Axios client + chatApi + practiceApi)
│   │   └── hooks/              # useAuth.jsx
│   ├── nginx.conf              # Production nginx config (proxies /api and /ai-api)
│   └── vite.config.js          # Dev proxy: /api → :8080, /ai-api → :8000
├── database/                    # Persistent volumes (postgres_data, ai_data)
├── scripts/                     # Deployment & maintenance shell scripts
│   ├── deploy.sh
│   ├── deploy_nodocker.sh
│   ├── setup_server.sh
│   ├── backup_database.sh
│   ├── restore_database.sh
│   └── check_db_status.sh
├── docs/                        # Project notes
├── docker-compose.yml
└── .env.example
```

## Quick Start

### 1. Prerequisites
- Docker + Docker Compose
- NVIDIA GPU (for embedding model) — or use CPU (slower)
- OpenRouter API key
- HuggingFace token (for `trunghieu1206/lawchatbot-40k` LoRA adapter)

### 2. Environment Setup
```bash
cp .env.example .env
# Edit .env and fill in:
# - OPENROUTER_API_KEY
# - HF_TOKEN
# - JWT_SECRET (use a long random string)
```

Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required.** OpenRouter API key |
| `HF_TOKEN` | — | **Required.** HuggingFace token for LoRA adapter |
| `JWT_SECRET` | — | **Required.** Secret for signing JWTs |
| `POSTGRES_USER` | `postgres` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `postgres` | PostgreSQL password |
| `COLLECTION_NAME` | `legal_rag_lora` | Milvus collection name |
| `EMBEDDING_ADAPTER` | `trunghieu1206/lawchatbot-40k` | HuggingFace LoRA adapter |
| `LLM_MODEL` | `google/gemini-2.5-flash` | LLM via OpenRouter |
| `TOP_K` | `15` | Number of retrieved chunks |

### 3. Ingest Legal Data (one-time, if not using the pre-built DB)

The repo includes a pre-built `ai-service/VN_law_lora.db` (Milvus Lite). If you need to rebuild from source data:

```bash
cd ai-service && pip install -r requirements.txt

# 1. Parse DOCX source files (optional)
python scripts/parse_docx.py

# 2. Ingest laws into PostgreSQL
python scripts/ingest_laws.py --file data/blhs_2015.json --source "BLHS 2015 (sửa đổi 2017)" --date 2018-01-01

# 3. Embed laws into Milvus Lite
python scripts/embed_laws.py
```

### 4. Run with Docker Compose
```bash
docker-compose up --build
```

Services:
- **Frontend**: http://localhost (port 80)
- **Backend API**: http://localhost:8080
- **AI Service**: http://localhost:8000
- **PostgreSQL**: localhost:5432

> **Note:** The AI service mounts `./database/ai_data` as `/data` inside the container. Place your `VN_law_lora.db` there, or let the ingestion scripts populate it.

### 5. Development Mode (without Docker)

**AI Service:**
```bash
cd ai-service
cp .env.example .env   # fill in values
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Backend:**
```bash
cd backend
# Set env vars or update application.yml
mvn spring-boot:run
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev   # runs on http://localhost:3000
# Vite proxies: /api → :8080, /ai-api → :8000
```

## Authentication & Session Persistence

The system supports **optional authentication** — users can chat as guests or create accounts to persist sessions.

### Guest Mode (No Login)
- Chat immediately without signup
- Sessions stored locally in browser
- Data lost if browser cache is cleared

### Authenticated Mode (With Login)
- Create account with email & password (8+ characters)
- All chat sessions automatically saved to PostgreSQL
- Access chat history from any device after logging in
- Sessions persist permanently

### Test Account (Auto-Created on First Startup)
```
Email: hieu@gmail.com
Password: hieu
```

> This account is automatically created when the backend starts. You can use it to test the authenticated flow.

For detailed authentication setup, see [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## API Overview

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register new user |
| POST | `/api/auth/login` | Login, get JWT token |

### Chat — Guest (no login required)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/guest/{guestId}/sessions` | Create a guest session |
| GET | `/api/chat/guest/{guestId}/sessions` | List guest's sessions |

### Chat — Authenticated
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/sessions` | Create a new session (JWT required) |
| GET | `/api/chat/sessions` | List user's sessions (JWT required) |

### Chat — Messages (guest & authenticated, by sessionId)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/sessions/{sessionId}/messages` | Send message (calls AI service) |
| GET | `/api/chat/sessions/{sessionId}/messages` | Get conversation history |
| DELETE | `/api/chat/sessions/{sessionId}` | Delete a session |

### AI Service (internal + proxied via `/ai-api/`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/predict` | Legal analysis (RAG + LangGraph pipeline) |
| POST | `/practice/evaluate` | Score & feedback for user's legal analysis (Practice Mode) |
| GET | `/health` | Health check + device status (CPU/GPU) |

## LangGraph Pipeline (`/predict`)

The pipeline uses intent classification to route each request:

```
START
  └─ classify_intent ──► casual     → casual_respond → END
                    ──► followup    → followup_generate → END
                    ──► new_case    → rewrite → retrieve → grade_documents
                                         ↓ (relevant?)
                                    extract_facts → map_laws → generate → END
                                         ↓ (rebuttal mode?)
                                    rebuttal → END
```

Nodes:
- **classify_intent** — Routes to `casual`, `followup`, or `new_case`
- **rewrite** — Rewrites query with role-specific legal keywords for better retrieval
- **retrieve** — Semantic search in Milvus Lite with LoRA-finetuned BGE-M3
- **grade_documents** — LLM-based relevance filtering of retrieved chunks
- **extract_facts** — Structured JSON extraction of case facts (dates, offenses, aggravating/mitigating factors)
- **map_laws** — Maps extracted facts to specific BLHS articles
- **generate** — Role-specific legal argument generation (defense / victim / neutral judge)
- **rebuttal** — Counter-argument generation against an opposing argument
- **followup_generate** — Contextual follow-up answers using session history
- **casual_respond** — Handles greetings and off-topic messages

## Roles (Bias Modes)
| Role | Vietnamese | Description |
|------|-----------|-------------|
| `defense` | Luật sư Bào chữa | Minimize sentence, find mitigating factors |
| `victim` | Luật sư Bị hại | Maximize sentence, maximize civil compensation |
| `neutral` | Thẩm phán | Objective, balanced judgment |

## Practice Mode (`/practice/evaluate`)

Users write their own legal analysis for a given case, then submit it to the AI for grading.

**Request:**
```json
{
  "case_description": "...",
  "user_mode": "defense | victim | neutral",
  "user_analysis": "..."
}
```

**Response:**
```json
{
  "score": 75,
  "feedback": {
    "strengths": ["..."],
    "improvements": ["..."],
    "missed_articles": ["Điều 51 BLHS (tình tiết giảm nhẹ)"],
    "suggestion": "..."
  }
}
```

## Features
- ✅ Guest mode — no login required (sessions identified by `guestId`)
- ✅ JWT authentication for registered users
- ✅ RAG with Milvus Lite (local `.db` file, no separate Milvus server)
- ✅ LoRA-finetuned BGE-M3 embedding (`trunghieu1206/lawchatbot-40k`)
- ✅ LangGraph pipeline with intent classification: Casual / Follow-up / New Case
- ✅ LangGraph nodes: Rewrite → Retrieve → Grade → Extract Facts → Map Laws → Generate
- ✅ Deterministic sentencing calculations (detention months, victim/defendant age at crime)
- ✅ Rebuttal (counter-argument) mode
- ✅ Practice Mode with AI scoring and structured feedback
- ✅ Role-specific legal argument generation (defense, victim, neutral)
- ✅ Chat history persistence (PostgreSQL)
- ✅ Docker Compose deployment with optional GPU support

## Technology Stack
- **AI Service**: Python 3.x, FastAPI 0.111, LangGraph, LangChain, Milvus Lite (pymilvus), BGE-M3 + PEFT/LoRA, OpenRouter (gemini-2.5-flash)
- **Backend**: Java 21, Spring Boot 3.4, Spring Security (JWT via jjwt 0.12.6), Spring Data JPA, WebFlux (WebClient), PostgreSQL, Bucket4j (rate limiting), Lombok
- **Frontend**: React 19, Vite 6, React Router v7, React Markdown, Axios, date-fns, CSS Modules
- **Infrastructure**: Docker Compose, nginx, PostgreSQL 16

## Deployment Scripts

Located in `scripts/`:

| Script | Description |
|--------|-------------|
| `deploy.sh` | Docker-based deployment |
| `deploy_nodocker.sh` | Bare-metal deployment without Docker |
| `setup_server.sh` | Initial server setup |
| `backup_database.sh` | PostgreSQL backup |
| `restore_database.sh` | PostgreSQL restore from backup |
| `check_db_status.sh` | Database health check |
