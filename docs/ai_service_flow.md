# AI Service — LangGraph Flow

## Full Graph

```mermaid
flowchart TD
    START(["▶ START\n(user message arrives)"])

    INTENT{{"classify_intent()\n3-way router"}}

    CASUAL["casual_respond\n─────────────────\nGreeting → bot intro\nOff-topic → polite decline"]

    REWRITE["rewrite\n─────────────────\nRewrite query to\noptimised legal search terms\n(role-aware)"]

    RETRIEVE["retrieve\n─────────────────\nMilvus vector search\nTOP_K=15 chunks"]

    GRADE["grade_documents\n─────────────────\nLLM grades each chunk:\nrelevant? yes / no"]

    RELEVANCE{{"check_relevance()\nAny relevant docs?"}}

    EXTRACT["extract_facts\n─────────────────\nLLM extracts structured\nlegal facts from case text\n(hanh_vi, ten_bi_cao, ...)"]

    REBUTTAL_CHECK{{"check_rebuttal()\nrebuttal_against set?"}}

    REBUTTAL["rebuttal\n─────────────────\nCounter-argument node\nused for debate mode"]

    MAP_LAWS["map_laws\n─────────────────\nMap facts → specific\narticles / clauses"]

    GENERATE["generate\n─────────────────\nRole-specific output:\n• Thẩm phán → Nhận định tòa án\n• Bào chữa → Luận điểm bào chữa\n• Bị hại → Bảo vệ bị hại"]

    FOLLOWUP["followup_generate\n─────────────────\nUses history + existing docs\nNo new Milvus retrieval\nHandles: explain, re-assess,\nnew evidence, questions"]

    END_NODE(["⏹ END\n(response returned)"])

    %% Main flow
    START --> INTENT

    INTENT -- "casual" --> CASUAL
    INTENT -- "followup" --> FOLLOWUP
    INTENT -- "new_case" --> REWRITE

    REWRITE --> RETRIEVE
    RETRIEVE --> GRADE
    GRADE --> RELEVANCE

    RELEVANCE -- "relevant docs found" --> EXTRACT
    RELEVANCE -- "no relevant docs\n(retry ≤ 2 times)" --> REWRITE

    EXTRACT --> REBUTTAL_CHECK
    REBUTTAL_CHECK -- "rebuttal_against is set" --> REBUTTAL
    REBUTTAL_CHECK -- "normal flow" --> MAP_LAWS

    MAP_LAWS --> GENERATE

    CASUAL --> END_NODE
    FOLLOWUP --> END_NODE
    GENERATE --> END_NODE
    REBUTTAL --> END_NODE
```

---

## Log Output Per Path

When a message is processed you will see these lines in the log:

### Path 1 — Casual / Greeting
```
  [INTENT] → casual | query='hi'
[NODE: casual_respond]
```

### Path 2 — Follow-up / Elaboration
```
  [INTENT] → followup | query='Giải thích thêm điểm 3...'
[NODE: followup_generate]
```

### Path 3 — New Case (full pipeline, no rebuttal)
```
  [INTENT] → new_case | query='Bị cáo Nguyễn Văn A tàng trữ...'
[NODE: rewrite]
[NODE: retrieve]
  [RAG] Retrieved 15 chunks:
    ID=1346  score=0.6106  | Chương: XX  Điều: 249
    ...
[NODE: grade_documents]
[NODE: extract_facts]
  Facts: ['hanh_vi', 'ten_bi_cao', ...]
  Sentencing data: {'detention_months': 5.1, ...}
[NODE: map_laws]
[NODE: generate]
```

### Path 3b — New Case with Rebuttal
```
  [INTENT] → new_case | query='...'
[NODE: rewrite]
[NODE: retrieve]
[NODE: grade_documents]
[NODE: extract_facts]
[NODE: rebuttal]
```

---

## State Fields

| Field | Type | Set by |
|-------|------|--------|
| `question` | `str` | `/predict` endpoint |
| `full_case_content` | `str` | `/predict` endpoint |
| `user_role` | `"neutral"\|"defense"\|"victim"` | `/predict` endpoint |
| `chat_history` | `List[{role, content}]` | `/predict` endpoint |
| `rebuttal_against` | `str\|None` | `/predict` endpoint |
| `documents` | `List[Document]` | `retrieve` node |
| `extracted_facts` | `Dict` | `extract_facts` node |
| `mapped_laws` | `List[Dict]` | `map_laws` node |
| `sentencing_data` | `Dict` | `extract_facts` node |
| `messages` | `List[BaseMessage]` | All terminal nodes |
| `retry_count` | `int` | `check_relevance` edge |
