# PostgreSQL Database — PenalLawChatbot

## Role of PostgreSQL in This System

PostgreSQL is the **application database** for the Spring Boot backend. It has **nothing to do with law content or vector search** — that responsibility belongs entirely to Milvus Lite (`VN_law_lora.db`).

PostgreSQL stores only:
- Registered user accounts (authentication)
- Guest/anonymous chat sessions
- The full conversation history (user messages + AI responses) for each session

```
User Request
     │
     ▼
Spring Boot Backend
     │
     ├─── PostgreSQL ──► stores session, messages, user accounts
     │
     └─── AI Service ──► Milvus Lite (vector search, law chunks)
```

---

## Tables

### 1. `users`

Stores registered user accounts for the authenticated (logged-in) flow.

> **Note:** Currently most functionality runs in guest mode — this table is pre-built for future authentication features.

| Column        | Type         | Constraints                | Description                                      |
|---------------|--------------|----------------------------|--------------------------------------------------|
| `id`          | `UUID`       | PK, auto-generated         | Unique user identifier                           |
| `email`       | `VARCHAR(255)`| UNIQUE, NOT NULL           | User's email address, used as login username     |
| `password_hash`| `TEXT`      | NOT NULL                   | Bcrypt-hashed password                           |
| `full_name`   | `VARCHAR(200)`| nullable                   | Display name                                     |
| `role`        | `VARCHAR(20)` | default `'user'`           | App role: `'user'` or `'admin'`                  |
| `is_active`   | `BOOLEAN`    | default `true`             | Whether the account is active                    |
| `created_at`  | `TIMESTAMP`  | auto, immutable            | Account creation time                            |

---

### 2. `chat_sessions`

Represents one conversation thread — either owned by a registered user or an anonymous guest.

| Column      | Type          | Constraints                | Description                                                       |
|-------------|---------------|----------------------------|-------------------------------------------------------------------|
| `id`        | `UUID`        | PK, auto-generated         | Unique session identifier                                         |
| `user_id`   | `UUID`        | FK → `users.id`, nullable  | Set if the session belongs to a logged-in user; null for guests   |
| `guest_id`  | `VARCHAR(64)` | nullable                   | Random client-generated ID stored in `localStorage` for guests    |
| `title`     | `VARCHAR(200)`| nullable                   | Auto-generated from the first message content (first 50 chars)    |
| `mode`      | `VARCHAR(20)` | default `'neutral'`        | Bias/role of the session: `'neutral'`, `'defense'`, or `'victim'`|
| `created_at`| `TIMESTAMP`   | auto, immutable            | When the session was created                                      |
| `updated_at`| `TIMESTAMP`   | auto-updated               | Last time a message was added or title changed                    |

**Relationships:**
- `user_id` → `users.id` (ManyToOne, optional)
- Has many → `chat_messages` (OneToMany, cascade delete)

---

### 3. `chat_messages`

Stores every individual message in a session — both from the user and the AI assistant.

| Column           | Type          | Constraints              | Description                                                                 |
|------------------|---------------|--------------------------|-----------------------------------------------------------------------------|
| `id`             | `UUID`        | PK, auto-generated       | Unique message identifier                                                   |
| `session_id`     | `UUID`        | FK → `chat_sessions.id`, NOT NULL | The session this message belongs to                                |
| `role`           | `VARCHAR(10)` | NOT NULL                 | Who sent it: `'user'` or `'assistant'`                                      |
| `content`        | `TEXT`        | NOT NULL                 | The full message text                                                        |
| `extracted_facts`| `TEXT`        | nullable                 | JSON string — structured legal facts extracted from the case (AI messages)  |
| `mapped_laws`    | `TEXT`        | nullable                 | JSON string — list of matched legal articles with article number, chapter, etc. |
| `created_at`     | `TIMESTAMP`   | auto, immutable          | When the message was sent                                                   |

**Notes on JSON columns:**

`extracted_facts` example value:
```json
{
  "hanh_vi": "tàng trữ trái phép chất ma túy",
  "ten_bi_cao": "Nguyễn Văn A",
  "co_tien_an": false,
  "tinh_tiet_giam_nhe": ["thành khẩn khai báo"]
}
```

`mapped_laws` example value:
```json
[
  { "article_number": "249", "chapter": "XVIII", "title": "Tội tàng trữ trái phép chất ma túy", "source": "BLHS 2015" },
  { "article_number": "250", "chapter": "XVIII", "title": "Tội vận chuyển trái phép chất ma túy", "source": "BLHS 2015" }
]
```

---

## Entity Relationships

```
users (1) ──────────────── (0..N) chat_sessions
                                       │
                                       │ (1)
                                       │
                                  (0..N) chat_messages
```

---

## What PostgreSQL Does NOT Store

| Data | Where it lives |
|------|---------------|
| Law article content (điều luật) | Milvus Lite `.db` file |
| Embedding vectors | Milvus Lite `.db` file |
| AI model weights / LoRA adapter | Hugging Face Hub |
| JWT secret / API keys | `.env` environment variables |
