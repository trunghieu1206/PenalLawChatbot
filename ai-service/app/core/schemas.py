"""
core/schemas.py — VNPLaw AI Service
LangGraph AgentState definition.
"""
from typing import List, Literal, Annotated, Sequence, Optional, Dict, Any
from langchain_core.messages import BaseMessage
from langchain_core.documents import Document
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages:            Annotated[Sequence[BaseMessage], add_messages]
    question:            str
    full_case_content:   str
    documents:           List[Document]
    retrieval_queries:   List[str]                   # 3 queries from multi_query_rewrite
    user_role:           Literal["defense", "victim", "neutral"]
    extracted_facts:     Optional[Dict[str, Any]]
    mapped_laws:         Optional[List[Dict[str, Any]]]
    sentencing_data:     Optional[Dict[str, Any]]
    chat_history:        Optional[List[Dict[str, Any]]]
    _missing_fields:     Optional[List[str]]         # set by clarification_check_node
    per_defendant_dates: Optional[List[Dict[str, str]]]  # multi-defendant support
    is_practice_mode:    Optional[bool]              # True when invoked from /practice/evaluate
    user_analysis:       Optional[str]               # user's written legal analysis (practice mode only)
