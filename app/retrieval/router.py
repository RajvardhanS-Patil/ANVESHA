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
