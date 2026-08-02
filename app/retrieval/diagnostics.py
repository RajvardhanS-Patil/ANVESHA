"""
ANVESHA RAG Failure Diagnostics — Automated failure classification endpoint.

Inspired by the rag_failure_diagnostics_clinic from awesome-llm-apps.
Classifies failed queries against 12 reusable failure patterns (P01–P12)
and suggests structural fixes.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.providers.llm_router import get_llm_router, Provider

logger = logging.getLogger(__name__)

router = APIRouter()

# === 12 RAG Failure Patterns ===
FAILURE_PATTERNS = {
    "P01": {
        "name": "Retrieval hallucination / grounding drift",
        "symptom": "Answer confidently contradicts retrieved documents.",
        "fix": "Strengthen entailment gate; lower confidence threshold; add claim-level citation check.",
    },
    "P02": {
        "name": "Chunk boundary or segmentation bug",
        "symptom": "Relevant facts are split or truncated across chunks.",
        "fix": "Use overlapping chunking (50-100 token overlap); increase chunk size; implement cross-chunk entity merging.",
    },
    "P03": {
        "name": "Embedding mismatch / semantic vs vector distance",
        "symptom": "Cosine similarity does not match true relevance.",
        "fix": "Use domain-tuned embeddings; add keyword/BM25 hybrid search fallback; lower min_score threshold.",
    },
    "P04": {
        "name": "Index skew or staleness",
        "symptom": "Old or missing data even though source of truth is updated.",
        "fix": "Re-index after document updates; add ingestion timestamp checks; implement incremental indexing.",
    },
    "P05": {
        "name": "Query rewriting or router misalignment",
        "symptom": "Router sends queries to the wrong tool or dataset.",
        "fix": "Add query classification step; improve corrective RAG rewrite; log rewrite decisions.",
    },
    "P06": {
        "name": "Long-chain reasoning drift",
        "symptom": "Multi-step tasks gradually lose track of earlier constraints.",
        "fix": "Limit k-hops; serialize reasoning chains explicitly; add intermediate verification steps.",
    },
    "P07": {
        "name": "Tool-call misuse or ungrounded tools",
        "symptom": "Tools are called with wrong arguments or without grounding.",
        "fix": "Add argument validation before tool calls; ground tool inputs in evidence.",
    },
    "P08": {
        "name": "Session memory leak / missing context",
        "symptom": "Conversation loses important facts between turns or sessions.",
        "fix": "Implement conversation state tracking; persist critical entities across turns.",
    },
    "P09": {
        "name": "Evaluation blind spots",
        "symptom": "System passes tests but fails on real incidents.",
        "fix": "Add adversarial test cases; expand gold set with production failure examples.",
    },
    "P10": {
        "name": "Startup ordering / dependency not ready",
        "symptom": "Services crash or 5xx during the first minutes after deploy.",
        "fix": "Add health check dependencies; implement retry-with-backoff on startup.",
    },
    "P11": {
        "name": "Config or secrets drift across environments",
        "symptom": "Works locally, breaks only in staging / prod due to settings.",
        "fix": "Validate all env vars at startup; add config diff check between environments.",
    },
    "P12": {
        "name": "Multi-tenant / multi-agent interference",
        "symptom": "Requests or agents step on each other's state or resources.",
        "fix": "Isolate agent state per request; add request-scoped contexts; audit shared mutable state.",
    },
}

DIAGNOSIS_SYSTEM_PROMPT = """You are a RAG failure diagnostics specialist.

You analyze failed RAG pipeline queries and classify them into one of 12 failure patterns.

FAILURE PATTERNS:
{patterns}

RULES:
1. Choose exactly ONE primary pattern (P01-P12).
2. Optionally choose up to TWO secondary candidates.
3. Explain your reasoning in short bullet points.
4. Propose a minimal structural fix specific to this incident.
5. Respond with JSON only."""

DIAGNOSIS_USER_PROMPT = """Classify this RAG pipeline failure:

QUESTION: {question}
ANSWER GENERATED: {answer}
EVIDENCE RETRIEVED: {evidence}
CONFIDENCE: {confidence}
ABSTAINED: {abstained}
ERROR (if any): {error}

Additional context:
- Seed entities found: {seed_count}
- Evidence nodes: {evidence_nodes}
- Evidence edges: {evidence_edges}
- Processing time: {processing_time}s

Respond with JSON:
{{
    "primary_pattern": "P01-P12",
    "primary_name": "pattern name",
    "secondary_candidates": ["P0X", "P0Y"],
    "reasoning": ["bullet 1", "bullet 2", "bullet 3"],
    "suggested_fix": "specific fix for this incident",
    "severity": "critical | high | medium | low"
}}"""


class DiagnosticRequest(BaseModel):
    """Request to classify a RAG failure."""
    question: str = Field(..., description="The query that failed")
    answer: str = Field(default="", description="The answer that was generated (if any)")
    evidence: str = Field(default="", description="The evidence that was retrieved (if any)")
    confidence: int = Field(default=0, description="Confidence score")
    abstained: bool = Field(default=False, description="Whether the system abstained")
    error: Optional[str] = Field(default=None, description="Error message if the pipeline crashed")
    seed_count: int = Field(default=0)
    evidence_nodes: int = Field(default=0)
    evidence_edges: int = Field(default=0)
    processing_time: float = Field(default=0.0)


@router.post("/diagnostics/classify")
async def classify_rag_failure(request: DiagnosticRequest):
    """
    Classify a RAG pipeline failure against 12 reusable patterns (P01–P12).

    Takes a failed query's data and returns:
    - Primary failure pattern with reasoning
    - Up to 2 secondary candidates
    - Specific structural fix suggestion
    - Severity rating
    """
    llm_router = get_llm_router()

    # Format the pattern descriptions for the prompt
    patterns_text = "\n".join(
        f"- {pid}: {p['name']} — {p['symptom']}"
        for pid, p in FAILURE_PATTERNS.items()
    )

    system_prompt = DIAGNOSIS_SYSTEM_PROMPT.format(patterns=patterns_text)
    user_prompt = DIAGNOSIS_USER_PROMPT.format(
        question=request.question,
        answer=request.answer[:1000] if request.answer else "(no answer generated)",
        evidence=request.evidence[:1500] if request.evidence else "(no evidence retrieved)",
        confidence=request.confidence,
        abstained=request.abstained,
        error=request.error or "none",
        seed_count=request.seed_count,
        evidence_nodes=request.evidence_nodes,
        evidence_edges=request.evidence_edges,
        processing_time=request.processing_time,
    )

    try:
        result = await llm_router.generate_json(
            prompt=user_prompt,
            system_prompt=system_prompt,
            provider=Provider.GROQ,
            temperature=0.0,
        )

        if "error" in result:
            raise HTTPException(status_code=500, detail=f"Diagnosis LLM call failed: {result['error']}")

        # Enrich with pattern details
        primary_id = result.get("primary_pattern", "P01")
        pattern_detail = FAILURE_PATTERNS.get(primary_id, {})

        return {
            "diagnosis": result,
            "pattern_detail": {
                "id": primary_id,
                "name": pattern_detail.get("name", "Unknown"),
                "symptom": pattern_detail.get("symptom", ""),
                "recommended_fix": pattern_detail.get("fix", ""),
            },
            "input_summary": {
                "question": request.question[:100],
                "had_evidence": bool(request.evidence),
                "had_answer": bool(request.answer),
                "confidence": request.confidence,
                "abstained": request.abstained,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG failure diagnosis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diagnostics/patterns")
async def list_failure_patterns():
    """List all 12 RAG failure patterns with descriptions and fixes."""
    return {
        "total_patterns": len(FAILURE_PATTERNS),
        "patterns": [
            {"id": pid, **details}
            for pid, details in FAILURE_PATTERNS.items()
        ],
    }
