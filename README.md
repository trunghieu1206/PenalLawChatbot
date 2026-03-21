# ⚖️ LegalAI — Vietnamese Legal AI Chatbot

Hệ thống tư vấn pháp lý hình sự thông minh dựa trên AI, RAG, và LangGraph.

## Architecture

```
React Frontend (Vite)
       ↓ REST API (HTTPS)
Spring Boot Backend (Auth, Chat Sessions, History)
       ↓ Internal HTTP
Python FastAPI AI Service (RAG + LangGraph + LLM)
       ↓
Milvus (semantic search) + PostgreSQL (storage)
       ↓
OpenRouter API (Gemini 2.5 Flash)
```

## Project Structure

```
PenalLawChatbot/
├── ai-service/               # Python FastAPI AI service
│   ├── app/
│   │   └── main.py          # LangGraph pipeline (RAG, fact extraction, sentencing)
│   ├── scripts/
│   │   ├── ingest_laws.py   # Data ingestion from JSON/CSV to PostgreSQL
│   │   └── embed_laws.py    # Batch embedding to Milvus
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── backend/                  # Spring Boot API Gateway
│   └── src/main/java/com/penallaw/backend/
│       ├── controller/      # AuthController, ChatController
│       ├── service/         # AuthService, ChatService
│       ├── entity/          # User, ChatSession, ChatMessage
│       ├── repository/      # JPA repositories
│       ├── security/        # JwtService, JwtAuthenticationFilter
│       ├── config/          # SecurityConfig
│       ├── client/          # AiServiceClient (WebClient)
│       ├── dto/             # AuthDTOs, ChatDTOs
│       └── exception/       # GlobalExceptionHandler
├── frontend/                 # React + Vite
│   └── src/
│       ├── pages/           # LoginPage, RegisterPage, ChatPage, TrainingPage
│       ├── components/      # RoleSelector, MessageBubble
│       ├── services/        # api.js (Axios client)
│       └── hooks/           # useAuth.jsx
├── server.py                 # Original AI server (kept for reference)
├── docker-compose.yml
└── .env.example
```

## Quick Start

### 1. Prerequisites
- Docker + Docker Compose
- NVIDIA GPU (for embedding model) — or use CPU (slower)
- OpenRouter API key
- HuggingFace token (for `trunghieu1206/lawchatbot-40k` adapter)

### 2. Environment Setup
```bash
cp .env.example .env
# Edit .env and fill in:
# - OPENROUTER_API_KEY
# - HF_TOKEN
# - JWT_SECRET (use a long random string)
```

### 3. Ingest Legal Data (one-time)
```bash
# Install Python deps locally
cd ai-service && pip install -r requirements.txt

# 1. Ingest laws into PostgreSQL
python scripts/ingest_laws.py --file data/blhs_2015.json --source "BLHS 2015 (sửa đổi 2017)" --date 2018-01-01

# 2. Embed laws into Milvus
python scripts/embed_laws.py
```

### 4. Run with Docker Compose
```bash
docker-compose up --build
```

Services:
- **Frontend**: http://localhost
- **Backend API**: http://localhost:8080
- **AI Service**: http://localhost:8000
- **PostgreSQL**: localhost:5432

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
```

## API Overview

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register new user |
| POST | `/api/auth/login` | Login, get JWT token |

### Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/sessions` | Create new chat session |
| GET | `/api/chat/sessions` | List user's sessions |
| POST | `/api/chat/sessions/{id}/messages` | Send message (calls AI) |
| GET | `/api/chat/sessions/{id}/messages` | Get chat history |

### AI Service (internal)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/predict` | Legal analysis (role, rebuttal support) |
| GET | `/health` | Health check + GPU status |

## Roles (Bias Modes)
| Role | Vietnamese | Description |
|------|-----------|-------------|
| `defense` | Luật sư Bào chữa | Minimize sentence for defendant |
| `victim` | Luật sư Bị hại | Maximize sentence and compensation |
| `neutral` | Thẩm phán | Objective, balanced judgment |

## Features
- ✅ RAG with Milvus (semantic) + PostgreSQL (full-text)
- ✅ LoRA-finetuned BGE-M3 embedding (`trunghieu1206/lawchatbot-40k`)
- ✅ LangGraph pipeline: Rewrite → Retrieve → Grade → Extract Facts → Map Laws → Generate
- ✅ Deterministic sentencing calculations (detention time, victim age)
- ✅ Law citation highlighting in UI (Điều X highlighted)
- ✅ Counter-argument (rebuttal) mode
- ✅ Training/practice mode with scoring
- ✅ JWT authentication
- ✅ Chat history persistence (PostgreSQL)
- ✅ Docker Compose deployment with GPU support

## Technology Stack
- **AI Service**: Python 3.11, FastAPI, LangGraph, LangChain, Milvus, BGE-M3 + LoRA, OpenRouter
- **Backend**: Java 21, Spring Boot 3.3, Spring Security (JWT), PostgreSQL, WebFlux
- **Frontend**: React 18, Vite, React Router v6, React Markdown, Axios, CSS Modules
- **Infrastructure**: Docker Compose, nginx, PostgreSQL 16
