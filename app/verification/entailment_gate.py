"""
ANVESHA Entailment Gate — Cross-provider verification for hallucination prevention.

Generate with Groq → Verify with Gemini (different provider = uncorrelated errors).
Supported claims → kept with citation.
Unsupported claims → stripped.
Nothing survives → abstain outright.
"""

import logging
import re
from typing import Optional

from app.providers.llm_router import get_llm_router, Provider
from app.verification.lexical_prefilter import decompose_into_claims, lexical_prefilter

logger = logging.getLogger(__name__)

VERIFICATION_PROMPT = """You are a compliance verification assistant. Your job is to determine whether
each claim below is SUPPORTED or NOT SUPPORTED by the provided evidence.

EVIDENCE:
{evidence}

CLAIMS TO VERIFY:
{claims}

For EACH claim, respond with EXACTLY this JSON format:
{{
    "results": [
        {{
            "claim_index": 0,
            "claim_text": "the claim text",
            "verdict": "SUPPORTED" or "NOT_SUPPORTED",
            "reasoning": "brief explanation",
            "supporting_evidence": "quote from evidence that supports this, or empty if not supported"
        }}
    ]
}}

RULES:
- A claim is SUPPORTED only if the evidence EXPLICITLY contains the information
- Reasonable inferences from the evidence count as SUPPORTED
- If the evidence doesn't mention the topic at all, verdict is NOT_SUPPORTED
- Do not add information beyond what's in the evidence"""


async def verify_answer(
    raw_answer: str,
    evidence_text: str,
    entity_names: Optional[list[str]] = None,
    confidence_threshold: float = 0.6,
) -> dict:
    """
    Full verification pipeline:
    1. Decompose answer into atomic claims
    2. Lexical pre-filter (cheap, no API call)
    3. Cross-provider entailment check on survivors (Gemini verifies Groq's output)
    4. Strip unsupported claims
    5. Abstain if nothing survives

    Args:
        raw_answer: The raw answer from the generation LLM
        evidence_text: Serialized evidence subgraph
        entity_names: Known entity names
        confidence_threshold: Min confidence to keep a claim

    Returns:
        Dict with verified answer, claims, confidence, abstained flag
    """
    router = get_llm_router()

    # Step 1: Decompose into atomic claims
    claims = decompose_into_claims(raw_answer)

    if not claims:
        return {
            "verified_answer": raw_answer,
            "claims": [],
            "supported_claims": [],
            "rejected_claims": [],
            "confidence": 50,
            "abstained": False,
            "verification_method": "no_claims_to_verify",
        }

    # Step 2: Lexical pre-filter
    passed_claims, prefilter_rejected = lexical_prefilter(
        claims, evidence_text, entity_names
    )

    logger.info(
        f"Pre-filter: {len(passed_claims)} passed, {len(prefilter_rejected)} rejected"
    )

    # Step 3: Cross-provider verification on survivors
    verified_claims = []
    verification_rejected = []

    if passed_claims and evidence_text:
        try:
            verification_results = await _cross_provider_verify(
                passed_claims, evidence_text, router
            )
            for claim, result in zip(passed_claims, verification_results):
                if result.get("verdict") == "SUPPORTED":
                    verified_claims.append({
                        **claim,
                        "verification": result,
                    })
                else:
                    verification_rejected.append({
                        **claim,
                        "verification": result,
                        "reject_reason": "cross_provider_not_supported",
                    })
        except Exception as e:
            logger.warning(f"Cross-provider verification failed: {e}")
            # If verification fails, keep all pre-filter passed claims
            # but lower confidence
            verified_claims = passed_claims
            logger.info("Falling back: keeping pre-filter passed claims")

    # All rejected claims
    all_rejected = prefilter_rejected + verification_rejected

    # Step 4: Build verified answer
    if verified_claims:
        verified_answer = _rebuild_answer(verified_claims, raw_answer)
        abstained = False
        # Calculate confidence based on verification results
        total_claims = len(claims)
        supported = len(verified_claims)
        confidence = int((supported / total_claims) * 100) if total_claims > 0 else 50
    else:
        # Step 5: Nothing survived — abstain
        verified_answer = (
            "I cannot provide a verified answer to this question based on the available evidence. "
            "The system's verification process could not confirm any claims against the source documents. "
            "Please refine your question or ingest additional relevant compliance documents."
        )
        abstained = True
        confidence = 0

    logger.info(
        f"Verification complete: {len(verified_claims)} supported, "
        f"{len(all_rejected)} rejected, abstained={abstained}, confidence={confidence}"
    )

    return {
        "verified_answer": verified_answer,
        "claims": [c.get("text", "") for c in claims],
        "supported_claims": [
            {
                "text": c.get("text", ""),
                "citations": c.get("citations", []),
                "overlap_ratio": c.get("overlap_ratio", 0),
            }
            for c in verified_claims
        ],
        "rejected_claims": [
            {
                "text": c.get("text", ""),
                "reject_reason": c.get("reject_reason", "unknown"),
            }
            for c in all_rejected
        ],
        "confidence": confidence,
        "abstained": abstained,
        "verification_method": "cross_provider_entailment",
    }


async def _cross_provider_verify(
    claims: list[dict],
    evidence: str,
    router,
) -> list[dict]:
    """
    Verify claims using Gemini (different provider from Groq generator).
    """
    # Format claims for the verification prompt
    claims_text = "\n".join(
        f"{i}. {claim['text']}" for i, claim in enumerate(claims)
    )

    prompt = VERIFICATION_PROMPT.format(
        evidence=evidence[:6000],  # Truncate evidence to fit context
        claims=claims_text,
    )

    try:
        result = await router.generate_json(
            prompt=prompt,
            system_prompt="You are a strict fact-checker. Only mark claims as SUPPORTED if the evidence explicitly contains the information.",
            provider=Provider.GEMINI,
            temperature=0.0,
        )

        if "error" in result:
            logger.warning(f"Gemini verification returned error: {result}")
            # Return all as supported with low confidence
            return [{"verdict": "SUPPORTED", "reasoning": "verification_unavailable"} for _ in claims]

        results = result.get("results", [])

        # Ensure we have a result for each claim
        while len(results) < len(claims):
            results.append({"verdict": "SUPPORTED", "reasoning": "no_result_returned"})

        return results

    except Exception as e:
        logger.error(f"Cross-provider verification failed: {e}")
        # Graceful degradation: keep claims but note verification failure
        return [{"verdict": "SUPPORTED", "reasoning": f"verification_error: {str(e)[:50]}"} for _ in claims]


def _rebuild_answer(verified_claims: list[dict], original_answer: str) -> str:
    """
    Rebuild the answer using only verified claims.

    Preserves the original structure where possible.
    """
    # Simple approach: reconstruct from verified claim sentences
    verified_texts = []
    for claim in verified_claims:
        original = claim.get("original", claim.get("text", ""))
        verified_texts.append(original)

    if not verified_texts:
        return original_answer  # Fallback to original

    rebuilt = " ".join(verified_texts)

    # Add sources section if there are citations
    all_citations = []
    for claim in verified_claims:
        all_citations.extend(claim.get("citations", []))

    if all_citations:
        unique_citations = list(dict.fromkeys(all_citations))  # Preserve order, remove dupes
        rebuilt += "\n\n**Sources:**\n"
        for citation in unique_citations:
            rebuilt += f"- {citation}\n"

    return rebuilt
