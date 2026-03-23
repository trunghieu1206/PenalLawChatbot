# backend and ai-service flows
User sends message
       │
       ▼
ChatController.sendMessage()
       │
       ▼
ChatService.sendMessage()
       │  calls aiServiceClient.predict(content, role, ...)
       ▼
AI Service (FastAPI) — LangGraph Workflow
       │
       ├─1─ extract_facts_node        → produces extractedFacts
       ├─2─ rewrite_question
       ├─3─ retrieve_node (Milvus)
       ├─4─ grade_documents
       ├─5─ map_laws_node             → produces mappedLaws
       └─6─ generate_judgment         → produces the answer text
       │
       ▼
PredictResponse { result, extracted_facts, mapped_laws, sentencing_data }
       │
       ▼
ChatService receives response → builds ChatMessage
       │
       ├── .content        = aiResponse.result()
       ├── .extractedFacts = aiResponse.extractedFacts()   ← saved as JSON TEXT
       └── .mappedLaws     = aiResponse.mappedLaws()       ← saved as JSON TEXT
       │
       ▼
messageRepository.save(aiMessage)  → PostgreSQL