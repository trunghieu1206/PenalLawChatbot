# ⚖️ VNPLaw Chatbot

> An AI-powered legal assistant for the Vietnamese Penal Code, built on a multi-agent Retrieval-Augmented Generation (RAG) pipeline.

PenalLawChatbot is a full-stack intelligent chatbot that helps users consult and understand Vietnamese criminal law. Given a user's question or case description, the system retrieves the most relevant legal articles from a pre-built vector database, applies cross-encoder reranking, performs temporal validity filtering, and then uses an LLM to generate a precise, source-cited legal response. An admin panel provides session management, visitor statistics, and user management.

---

## ✨ Key Features

- **Multi-query RAG pipeline** — Rewrites the user query into multiple search variants for broader recall, then deduplicates and reranks results.
- **Temporal filtering** — Automatically tags articles by effective date and prioritises the most legally current version.
- **LoRA fine-tuned embedding** — A custom Jina Embedding V5 Text Nano model fine-tuned on ~4 k Vietnamese legal case question pairs for domain-specific retrieval.
- **BM25 hybrid retrieval** — Combines dense vector search with sparse keyword search (BM25) for more robust results.
- **LangGraph agentic flow** — Nodes for fact extraction, clarification, law mapping, retrieval, reranking, generation, and answer verification are orchestrated as a stateful graph.
- **JWT authentication** — Secure user registration, login, and role-based access (user / admin).
- **Admin dashboard** — Visitor tracking, chat session viewer, and user management.

---

## 🏗️ System Architecture

```
User Browser
    │  HTTP :80
    ▼
 Nginx (reverse proxy)
    ├── /api/*     → Spring Boot Backend  :8080  (Auth, Sessions, Stats)
    └── /ai-api/*  → FastAPI AI Service   :8000  (LangGraph RAG pipeline)
                            │
                   ┌────────┴────────┐
                   │                 │
           Milvus Lite DB       PostgreSQL
          (VN_law_lora.db)    (Users, Sessions,
          Vector search         Laws, Logs)
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **AI Service** | Python 3.11, FastAPI, LangGraph, LangChain, PyTorch |
| **Embedding** | `trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora` (768-dim, LoRA fine-tuned) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` (multilingual cross-encoder) |
| **LLM** | `google/gemini-2.5-flash` via OpenRouter API |
| **Vector DB** | Milvus Lite (local `.db` file, `pymilvus 2.4.x`) |
| **BM25** | `rank-bm25` (hybrid keyword retrieval) |
| **Backend API** | Java 21, Spring Boot 3.4 |
| **Database** | PostgreSQL, Hibernate/JPA, Lombok |
| **Frontend** | React 19, Vite 6, Tailwind CSS 3 |
| **Web Server** | Nginx (static SPA + reverse proxy) |
| **Deployment** | AWS EC2 Ubuntu 24.04, bare-metal (no Docker) |

---

## 📁 Repository Structure

```
PenalLawChatbot/
├── ai-service/          # Python FastAPI + LangGraph RAG pipeline
│   ├── app/
│   │   ├── graph/
│   │   │   ├── builder.py          # LangGraph graph assembly
│   │   │   └── nodes/              # extract, retrieve, law_mapping, generation, verify
│   │   ├── services/               # embedding, reranking, BM25
│   │   └── main.py                 # FastAPI app + lifespan startup
│   └── requirements.txt
├── backend/             # Java Spring Boot REST API
│   └── src/main/java/com/penallaw/
├── frontend/            # React + Vite SPA
│   └── src/
├── database/
│   └── backups/         # PostgreSQL .sql backup files (gitignored)
└── scripts/
    ├── setup_server.sh       # One-time server environment setup
    ├── restore_database.sh   # Restore PostgreSQL from backup
    └── deploy_nodocker.sh    # Build & launch all 4 services
```

---

## 🚀 Installation Guide

This guide describes the complete steps to deploy on an **AWS EC2 Ubuntu 24.04** server **without Docker**.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Pre-Installation Checklist](#2-pre-installation-checklist)
3. [Connecting to the EC2 Instance](#3-connecting-to-the-ec2-instance)
4. [Step 1 — Install Server Environment](#4-step-1--install-server-environment)
5. [Step 2 — Upload Required Data Files](#5-step-2--upload-required-data-files)
6. [Step 3 — Configure Environment Variables (.env)](#6-step-3--configure-environment-variables-env)
7. [Step 4 — Restore the Database](#7-step-4--restore-the-database)
8. [Step 5 — Deploy All Services](#8-step-5--deploy-all-services)
9. [Post-Deployment Health Checks](#9-post-deployment-health-checks)
10. [Troubleshooting Common Errors](#10-troubleshooting-common-errors)
11. [Restarting Individual Services](#11-restarting-individual-services)

---

## 1. System Specifications

| Component        | Specifications                                       |
|------------------|------------------------------------------------------|
| **OS**           | Ubuntu 24.04 LTS (64-bit)                            |
| **RAM**          | 8 GB                                                 |
| **Storage**      | 20 GB                                                |
| **CPU**          | 2 vCPU or more                                       |
| **Open Ports**   | `22` (SSH), `80` (HTTP), `443` (HTTPS, optional)     |
| **Access Level** | Root (`sudo`) or `ubuntu` user with sudo privileges  |

> **Note:** The system runs on CPU-only (no GPU required). A GPU will significantly speed up AI inference if available.

---

## 2. Pre-Installation Checklist

Prepare the following information and resources **before you start**:

### 2.1. API Keys
- **`OPENROUTER_API_KEY`** — Get it at [openrouter.ai](https://openrouter.ai)
- **`HF_TOKEN`** — Get it at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) *(required to download the private LoRA embedding model)*

### 2.2. Files to Upload to the Server
| File | Description | Target Path on Server |
|------|-------------|------------------------|
| `penallaw_backup_*.sql` | PostgreSQL database backup | `/root/PenalLawChatbot/database/backups/` |
| `VN_law_lora.db` | Pre-embedded Milvus vector database | `/root/PenalLawChatbot/ai-service/` |
| `chatbot-key.pem` | EC2 SSH key pair | On your local machine |

### 2.3. AWS Security Group Configuration
In the AWS Console, open the Security Group for your instance and add the following **Inbound Rules**:

| Type  | Protocol | Port | Source    |
|-------|----------|------|-----------|
| SSH   | TCP      | 22   | Your IP   |
| HTTP  | TCP      | 80   | 0.0.0.0/0 |

---

## 3. Connecting to the EC2 Instance

From your local machine terminal, connect via SSH:

```bash
# Set correct permissions on the PEM file (only needed once)
chmod 400 chatbot-key.pem

# Connect to the server
ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP>

# Switch to root
sudo su -
```

> Replace `<EC2_PUBLIC_IP>` with the public IP address of your EC2 instance.

---

## 4. Step 1 — Install Server Environment

The `setup_server.sh` script automatically installs the entire required environment. It is safe to run multiple times — every step checks whether it is already installed before proceeding.

**What this script does:**
- Installs core system packages: `git`, `curl`, `nginx`, `build-essential`
- Installs **Python 3.11** (automatically handles cases where the default Python version is incompatible)
- Installs **Node.js 20 LTS**, **Java 21 JDK**, and **Maven**
- Installs **PostgreSQL**
- Clones the `PenalLawChatbot` repository from GitHub (`dev` branch)
- Installs **PyTorch** (GPU-enabled if a GPU is detected; CPU-only otherwise)
- Installs all Python dependencies from `ai-service/requirements.txt`
- Pre-downloads both AI models to local cache (prevents timeout on first startup):
  - `BAAI/bge-reranker-v2-m3`
  - `trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05`

**Run the script:**

```bash
# On the server (as root) — download and run directly
curl -fsSL https://raw.githubusercontent.com/trunghieu1206/PenalLawChatbot/dev/scripts/setup_server.sh -o setup_server.sh
chmod +x setup_server.sh
bash setup_server.sh
```

Or, if the repository has already been cloned:

```bash
cd /root/PenalLawChatbot
bash scripts/setup_server.sh
```

> ⏳ This script takes approximately **5–15 minutes** depending on the server's internet speed (primarily due to downloading PyTorch and the AI models).

**Log file is saved to:** `/root/penallaw_setup.log`

---

## 5. Step 2 — Upload Required Data Files

Run the following commands **from your local machine** (not on the server):

### 5.1. Upload the Database Backup

```bash
# Create the backup directory on the server first
ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP> \
    "sudo mkdir -p /root/PenalLawChatbot/database/backups"

# Upload the PostgreSQL backup file
scp -i chatbot-key.pem \
    ./database/backups/penallaw_backup_*.sql \
    ubuntu@<EC2_PUBLIC_IP>:/tmp/

# Move it to the correct directory (on the server)
ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP> \
    "sudo mv /tmp/penallaw_backup_*.sql /root/PenalLawChatbot/database/backups/"
```

### 5.2. Upload the Vector Database (Milvus)

The `VN_law_lora.db` file contains all pre-embedded legal articles using the LoRA fine-tuned model.

```bash
scp -i chatbot-key.pem \
    ./ai-service/VN_law_lora.db \
    ubuntu@<EC2_PUBLIC_IP>:/tmp/

ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP> \
    "sudo mkdir -p /root/PenalLawChatbot/ai-service && \
     sudo mv /tmp/VN_law_lora.db /root/PenalLawChatbot/ai-service/"
```

> ⏳ Uploading `VN_law_lora.db` may take several minutes depending on the file size and your network bandwidth.

---

## 6. Step 3 — Configure Environment Variables (.env)

On the server, create the `.env` file from the provided template:

```bash
cd /root/PenalLawChatbot

# Copy the template
cp .env.example .env

# Edit the file
nano .env
```

Fill in the **required** values:

```env
# ---- REQUIRED — must be filled in ----
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxx
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ---- Pre-filled — do not change ----
JWT_SECRET=j1WQjbYqkjImzp0etlJQRgI4alxtRxGTgAJalevJKKDAuuHFm2gbPNXcRxMzYNQ1nUJd6hYNVPkScjrEZr0aGA==
POSTGRES_DB=penallaw
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
```

Save the file: `Ctrl+X` → `Y` → `Enter`

> **Important:** `OPENROUTER_API_KEY` and `HF_TOKEN` are **mandatory**. The deployment script will error out and stop if either is missing.

---

## 7. Step 4 — Restore the Database

The `restore_database.sh` script will automatically:
- Start PostgreSQL if it is not already running
- Find the latest backup file in `database/backups/`
- Drop and recreate the `penallaw` database
- Import all data from the `.sql` file
- Report record counts after restoration (laws, visitor_logs, chat_sessions)

```bash
cd /root/PenalLawChatbot
bash scripts/restore_database.sh
```

If the database already contains data, the script will prompt for confirmation:
```
Type 'yes' to continue, or Ctrl+C to cancel:
yes
```

**A successful restore looks like this:**
```
✅  Database Ready!

  Database : penallaw
  Source   : penallaw_backup_20260628_031720.sql
  Tables   : 6
  Laws     : 1247 articles
  Visitors : 352 rows
  Sessions : 89 rows
```

---

## 8. Step 5 — Deploy All Services

The `deploy_nodocker.sh` script starts **4 services** in the correct order:

| Service | Port | Description |
|---------|------|-------------|
| **PostgreSQL** | 5432 | Relational database |
| **AI Service** (FastAPI/uvicorn) | 8000 | AI processing, LangGraph, RAG pipeline |
| **Backend** (Spring Boot) | 8080 | REST API, JWT authentication |
| **Frontend** (nginx) | 80 | React SPA + reverse proxy |

```bash
cd /root/PenalLawChatbot
bash scripts/deploy_nodocker.sh
```

**What this script does automatically:**
1. Verifies and installs any missing tools (Java, Maven, Node.js)
2. Pulls the latest code from the `dev` branch
3. Reads `.env` and validates all required keys
4. Starts PostgreSQL and auto-restores from backup if the database is empty
5. Checks for `VN_law_lora.db` and warns if it is missing
6. Installs Python dependencies (skips packages already installed)
7. Enforces the correct `pymilvus 2.4.x` version (version `3.x` is incompatible)
8. Builds the Spring Boot backend with Maven (skipped if source is unchanged)
9. Builds the React frontend with `npm run build` (skipped if source is unchanged)
10. Configures and starts nginx with reverse proxy rules
11. Waits and performs health checks on each service (up to 300 seconds)

> ⏳ **First run:** Takes approximately **5–10 minutes** for the Maven build and model loading.
> ⚡ **Subsequent runs:** Takes only **1–2 minutes** since build steps are skipped when source is unchanged.

**View live logs:**
```bash
tail -f /var/log/penallaw/ai-service.log   # AI Service
tail -f /var/log/penallaw/backend.log      # Spring Boot Backend
tail -f /var/log/penallaw/postgres.log     # PostgreSQL
tail -f /var/log/nginx/error.log           # nginx
```

---

## 9. Post-Deployment Health Checks

### 9.1. Check via Browser

Open your browser and navigate to:

```
http://<EC2_PUBLIC_IP>
```

You should see the VNPLaw Chatbot login interface.

### 9.2. Manual Health Check Commands

```bash
# AI Service
curl http://localhost:8000/health

# Spring Boot Backend
curl http://localhost:8080/actuator/health

# Frontend (via nginx)
curl -I http://localhost:80
```

### 9.3. Verify Running Processes

```bash
# AI service (uvicorn)
ps aux | grep uvicorn

# Backend (Java)
ps aux | grep java

# nginx
ps aux | grep nginx

# PostgreSQL
pg_isready
```

---

## 10. Troubleshooting Common Errors

### Error: `OPENROUTER_API_KEY not set in .env`
**Cause:** The `.env` file was not created or the key was not filled in.
**Fix:** Redo Step 3 and provide a valid API key.

---

### Error: `Milvus DB not found at .../VN_law_lora.db`
**Cause:** The vector database file was not uploaded.
**Fix:** Redo Step 5.2 to upload `VN_law_lora.db`.

```bash
scp -i chatbot-key.pem VN_law_lora.db ubuntu@<EC2_PUBLIC_IP>:/tmp/
ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP> \
    "sudo mv /tmp/VN_law_lora.db /root/PenalLawChatbot/ai-service/"
```

---

### Error: `No backup files found in .../database/backups`
**Cause:** The `.sql` backup file was not uploaded.
**Fix:** Redo Step 5.1 to upload the backup file.

---

### Git pull error: `untracked working tree files would be overwritten`
**Cause:** Local untracked files conflict with files incoming from the remote branch.
**Fix:**

```bash
# Remove the conflicting files (usually temporary .sql backup files)
rm database/backups/penallaw_backup_*.sql
git pull origin dev
```

Or, to completely discard all local changes:
```bash
git fetch origin
git reset --hard origin/dev
git clean -fd
```

---

### AI Service not ready after 300 seconds
**Cause:** Model download failed, or `HF_TOKEN` is missing/incorrect.
**Fix:** Check the log and verify the token:

```bash
tail -50 /var/log/penallaw/ai-service.log

# Verify HF_TOKEN is set correctly
grep HF_TOKEN /root/PenalLawChatbot/.env
```

---

### Backend cannot connect to the database
**Cause:** PostgreSQL is not running or credentials are incorrect.
**Fix:**

```bash
# Check PostgreSQL status
pg_isready

# Start it manually if not running
PG_VER=$(ls /etc/postgresql/ | sort -V | tail -1)
pg_ctlcluster $PG_VER main start
```

---

## 11. Restarting Individual Services

After a server reboot or when you need to restart everything, simply re-run the deploy script:

```bash
cd /root/PenalLawChatbot
bash scripts/deploy_nodocker.sh
```

To restart individual services manually:

```bash
# Restart AI Service
pkill -f uvicorn || true
_GC=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l || echo 0)
[ "$_GC" -lt 1 ] && _GC=1
cd /root/PenalLawChatbot/ai-service
GPU_COUNT=$_GC nohup python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 --workers $_GC \
    >> /var/log/penallaw/ai-service.log 2>&1 &

# Restart Backend
pkill -f "java.*penallaw" || pkill -f "java.*backend" || true
nohup java -jar /root/PenalLawChatbot/backend/target/*.jar \
    --server.port=8080 \
    >> /var/log/penallaw/backend.log 2>&1 &

# Restart nginx
pkill nginx || true && nginx

# Restart PostgreSQL
PG_VER=$(ls /etc/postgresql/ | sort -V | tail -1)
pg_ctlcluster $PG_VER main start
```

---

## Quick Reference

```bash
# 1. Connect to the server
ssh -i chatbot-key.pem ubuntu@<EC2_PUBLIC_IP>
sudo su -

# 2. Install environment (first time only)
bash scripts/setup_server.sh

# 3. Upload files from your local machine
scp -i chatbot-key.pem database/backups/penallaw_backup_*.sql ubuntu@<IP>:/tmp/
scp -i chatbot-key.pem ai-service/VN_law_lora.db ubuntu@<IP>:/tmp/

# 4. Move files to the correct locations (on the server)
mv /tmp/penallaw_backup_*.sql /root/PenalLawChatbot/database/backups/
mv /tmp/VN_law_lora.db /root/PenalLawChatbot/ai-service/

# 5. Configure environment
cp .env.example .env && nano .env

# 6. Restore the database
bash scripts/restore_database.sh

# 7. Deploy all services
bash scripts/deploy_nodocker.sh

# 8. Access the application
# Open browser: http://<EC2_PUBLIC_IP>
```
