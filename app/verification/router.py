"""
ANVESHA Verification Router — Endpoints for verified queries and evidence bundles.

Provides the full pipeline: query → retrieve → generate → verify → respond.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.retrieval.graphrag import query_graphrag, get_answer_store
from app.verification.entailment_gate import verify_answer
from app.verification.evidence_bundle import create_evidence_bundle, verify_bundle_integrity

logger = logging.getLogger(__name__)

router = APIRouter()


class VerifiedQueryRequest(BaseModel):
    """Request for a fully verified compliance Q&A query."""
    question: str = Field(..., description="The compliance question")
    k_hops: Optional[int] = Field(default=None)
    top_k_seeds: Optional[int] = Field(default=None)
    debate_mode: Optional[bool] = Field(default=False)
    as_of: Optional[str] = Field(default=None)


@router.post("/query/verified")
async def verified_compliance_query(request: VerifiedQueryRequest):
    """
    Ask a compliance question with full verification pipeline.

    Pipeline:
    1. GraphRAG retrieval + generation (Groq)
    2. Claim decomposition
    3. Lexical pre-filter
    4. Cross-provider verification (Gemini)
    5. Strip unsupported claims or abstain

    Or, in Debate Mode:
    1. GraphRAG retrieval
    2. Advocate (Groq) vs Skeptic (Groq) debate
    3. Gemini Judge adjudication and verdict

    Returns verified answer with confidence and evidence bundle.
    """
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    logger.info(f"Verified query: {request.question[:100]} (debate={request.debate_mode}, as_of={request.as_of})")

    if request.debate_mode:
        from app.retrieval.debate import run_compliance_debate
        debate_result = await run_compliance_debate(
            question=request.question,
            k_hops=request.k_hops,
            top_k_seeds=request.top_k_seeds,
            as_of=request.as_of,
        )
        return debate_result

    # Step 1: GraphRAG retrieval + generation
    graphrag_result = await query_graphrag(
        question=request.question,
        k_hops=request.k_hops,
        top_k_seeds=request.top_k_seeds,
        as_of=request.as_of,
    )

    raw_answer = graphrag_result.get("answer", "")
    evidence_subgraph = graphrag_result.get("evidence_subgraph", {})

    # Build evidence text for verification
    evidence_text = _build_evidence_text(evidence_subgraph)
    entity_names = [
        n.get("name", "") for n in evidence_subgraph.get("seed_entities", [])
    ]

    # Step 2-5: Verification pipeline
    verification = await verify_answer(
        raw_answer=raw_answer,
        evidence_text=evidence_text,
        entity_names=entity_names,
    )

    # Merge results
    result = {
        **graphrag_result,
        "verified_answer": verification["verified_answer"],
        "verification": {
            "method": verification["verification_method"],
            "total_claims": len(verification["claims"]),
            "supported_claims": verification["supported_claims"],
            "rejected_claims": verification["rejected_claims"],
            "abstained": verification["abstained"],
        },
        "confidence": verification["confidence"],
    }

    # Update answer store with verified result
    answer_store = get_answer_store()
    answer_store[graphrag_result["answer_id"]] = result

    return result


@router.get("/evidence/{answer_id}")
async def get_evidence_bundle(answer_id: str):
    """
    Download the signed evidence bundle for an answer.

    The bundle contains a SHA-256 hash of the evidence + answer,
    allowing independent verification that the response hasn't been tampered with.
    """
    answer_store = get_answer_store()

    if answer_id not in answer_store:
        raise HTTPException(
            status_code=404,
            detail=f"Answer {answer_id} not found"
        )

    answer_data = answer_store[answer_id]
    bundle = create_evidence_bundle(answer_data)

    return bundle


@router.post("/evidence/verify")
async def verify_evidence(bundle: dict):
    """
    Verify the integrity of an evidence bundle.

    Checks that the SHA-256 hash matches, confirming
    the bundle hasn't been tampered with.
    """
    result = verify_bundle_integrity(bundle)
    return result


def _build_evidence_text(subgraph: dict) -> str:
    """Build evidence text from subgraph for verification."""
    parts = []

    for entity in subgraph.get("seed_entities", []):
        parts.append(
            f"Entity: {entity.get('name', '?')} ({entity.get('type', '?')})"
        )

    for node in subgraph.get("nodes", []):
        name = node.get("name", "") if isinstance(node, dict) else str(node)
        parts.append(f"Node: {name}")

    for edge in subgraph.get("edges", []):
        if isinstance(edge, dict):
            parts.append(
                f"Relation: {edge.get('source', '?')} → {edge.get('target', '?')} ({edge.get('type', '?')})"
            )

    return "\n".join(parts) if parts else ""
