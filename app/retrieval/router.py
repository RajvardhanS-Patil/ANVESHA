"""
ANVESHA Retrieval Router — FastAPI endpoints for GraphRAG queries.

Endpoints:
- POST /query — Ask a compliance question
- GET /graph/subgraph/{answer_id} — Get evidence subgraph for an answer
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.retrieval.graphrag import query_graphrag, get_answer_store
from app.providers.llm_router import get_llm_router, Provider

logger = logging.getLogger(__name__)

router = APIRouter()


class QueryRequest(BaseModel):
    """Request body for compliance queries."""
    question: str = Field(..., description="The compliance question to answer")
    k_hops: Optional[int] = Field(default=None, description="Graph traversal hops (default: 2)")
    top_k_seeds: Optional[int] = Field(default=None, description="Vector search seed count (default: 10)")
    max_context_nodes: Optional[int] = Field(default=None, description="Max evidence nodes (default: 50)")


@router.post("/query")
async def compliance_query(request: QueryRequest):
    """
    Ask a compliance question using GraphRAG.

    The system:
    1. Embeds your question
    2. Finds relevant entities via vector similarity
    3. Traverses the knowledge graph for evidence
    4. Generates a grounded answer with citations
    5. Returns confidence score and evidence subgraph

    Every claim is backed by a traceable citation or the system abstains.
    """
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    logger.info(f"Query request: {request.question[:100]}")

    result = await query_graphrag(
        question=request.question,
        k_hops=request.k_hops,
        top_k_seeds=request.top_k_seeds,
        max_context_nodes=request.max_context_nodes,
    )

    return result


@router.get("/graph/subgraph/{answer_id}")
async def get_evidence_subgraph(answer_id: str):
    """
    Get the evidence subgraph for a specific answer.

    Used by the frontend to render the graph visualization
    for a particular compliance query result.
    """
    answer_store = get_answer_store()

    if answer_id not in answer_store:
        raise HTTPException(
            status_code=404,
            detail=f"Answer {answer_id} not found. Answers are stored in memory and may be lost on restart."
        )

    answer = answer_store[answer_id]
    return {
        "answer_id": answer_id,
        "question": answer.get("question", ""),
        "evidence_subgraph": answer.get("evidence_subgraph", {}),
        "confidence": answer.get("confidence", 0),
        "citations": answer.get("citations", []),
    }


@router.get("/answers")
async def list_answers():
    """List all stored answers (in-memory)."""
    answer_store = get_answer_store()
    return {
        "total_answers": len(answer_store),
        "answers": [
            {
                "answer_id": aid,
                "question": a.get("question", "")[:100],
                "confidence": a.get("confidence", 0),
                "citations_count": len(a.get("citations", [])),
                "generated_at": a.get("generated_at", ""),
            }
            for aid, a in answer_store.items()
        ],
    }

class DemoQueryRequest(BaseModel):
    question: str
    file_name: Optional[str] = None

@router.post("/demo/chat/groq")
async def demo_chat_groq(request: DemoQueryRequest):
    llm = get_llm_router()
    context = ""
    if request.file_name and "voltguard" in request.file_name.lower():
        context = """VoltGuard: A Fuzzy Inference-Driven Embedded System for Real-Time Electricity Theft Detection.
It uses an ESP32 edge node to capture electrical parameters.
It uses Firebase for cloud telemetry.
It uses a fuzzy inference-based anomaly detection system to prevent false alarms from inrush currents and voltage fluctuations.
Sudden load switching (inrush) is classified as 21-50% theft risk (Low Suspicion).
Voltage fluctuation is 51-80% (Moderate Suspicion).
Simulated theft is 81-100% (High Probability).
Normal operation is 0-20%."""
    else:
        context = """Apex Payments Security Policy.
§1.0 mandates AES-256 encryption for all customer data in transit and at rest.
§3.0 explicitly admits the legacy transactions_db PostgreSQL database currently stores user credit card numbers and passwords in plain text."""
        
    advocate_prompt = f"Context:\n{context}\n\nQuestion: {request.question}\n\nAct as the Advocate. Briefly argue the strengths or compliance of the system regarding the question based ONLY on the context."
    skeptic_prompt = f"Context:\n{context}\n\nQuestion: {request.question}\n\nAct as the Skeptic. Briefly point out the flaws, contradictions, or risks regarding the question based ONLY on the context."
    
    advocate_resp = await llm.generate(advocate_prompt, provider=Provider.GROQ)
    skeptic_resp = await llm.generate(skeptic_prompt, provider=Provider.GROQ)
    
    judge_prompt = f"Context:\n{context}\n\nQuestion: {request.question}\nAdvocate: {advocate_resp}\nSkeptic: {skeptic_resp}\n\nProvide a final verdict summarizing the situation based on the context. Structure your response clearly."
    judge_resp = await llm.generate(judge_prompt, provider=Provider.GROQ)
    
    return {
        "debate_mode": True,
        "verdict": "ANALYZED",
        "confidence": 88,
        "answer": judge_resp,
        "advocate_argument": advocate_resp,
        "skeptic_argument": skeptic_resp,
        "citations": [request.file_name or "Apex_Security_Policy.pdf"],
        "answer_id": "demo-q-groq"
    }
