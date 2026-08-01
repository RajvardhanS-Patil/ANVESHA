"""
ANVESHA Lexical Pre-filter — Cheap overlap check before spending an API call.

Rejects claims with near-zero word or embedding overlap against
the evidence subgraph immediately, saving expensive API calls.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum thresholds for a claim to pass the pre-filter
MIN_WORD_OVERLAP_RATIO = 0.15   # At least 15% of claim words must appear in evidence
MIN_ENTITY_OVERLAP = 1          # At least 1 entity name must appear in both


def decompose_into_claims(answer: str) -> list[dict]:
    """
    Decompose an answer into atomic claims.

    Each claim is a single factual statement that can be
    independently verified against evidence.
    """
    # Split by sentences
    sentences = re.split(r'(?<=[.!?])\s+', answer)

    claims = []
    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence or len(sentence) < 10:
            continue

        # Skip meta-sentences (confidence, sources headers, etc.)
        skip_patterns = [
            r"^(Confidence|Sources|References|Note|Disclaimer)",
            r"^\d+[./]\s*$",
            r"^[-*•]\s*$",
            r"^#{1,3}\s",
            r"cannot answer",
            r"insufficient evidence",
            r"not enough information",
        ]
        if any(re.match(p, sentence, re.IGNORECASE) for p in skip_patterns):
            continue

        # Check if sentence contains a factual claim (not just a citation)
        citation_only = re.match(r'^\[.*\]$', sentence)
        if citation_only:
            continue

        # Extract any inline citations
        citations = re.findall(r'\[Source:([^\]]+)\]', sentence)
        clean_sentence = re.sub(r'\[Source:[^\]]+\]', '', sentence).strip()

        if clean_sentence and len(clean_sentence) > 10:
            claims.append({
                "index": i,
                "text": clean_sentence,
                "original": sentence,
                "citations": [c.strip() for c in citations],
            })

    logger.info(f"Decomposed answer into {len(claims)} atomic claims")
    return claims


def lexical_prefilter(
    claims: list[dict],
    evidence_text: str,
    entity_names: Optional[list[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """
    Pre-filter claims based on lexical/embedding overlap with evidence.

    Args:
        claims: List of decomposed claims
        evidence_text: Serialized evidence subgraph text
        entity_names: Known entity names from the evidence

    Returns:
        Tuple of (passed_claims, rejected_claims)
    """
    if not evidence_text:
        # No evidence — reject all claims
        return [], claims

    evidence_lower = evidence_text.lower()
    evidence_words = set(_tokenize(evidence_lower))
    entity_set = {name.lower() for name in (entity_names or [])}

    passed = []
    rejected = []

    for claim in claims:
        claim_lower = claim["text"].lower()
        claim_words = set(_tokenize(claim_lower))

        if not claim_words:
            rejected.append({**claim, "reject_reason": "empty_claim"})
            continue

        # Check 1: Word overlap ratio
        overlap = claim_words & evidence_words
        overlap_ratio = len(overlap) / len(claim_words) if claim_words else 0

        # Check 2: Entity name overlap
        entity_overlap = 0
        if entity_set:
            for entity_name in entity_set:
                if entity_name in claim_lower:
                    entity_overlap += 1

        # Check 3: Key phrase presence
        # Extract multi-word phrases from the claim and check evidence
        key_phrases = _extract_key_phrases(claim["text"])
        phrase_matches = sum(1 for p in key_phrases if p.lower() in evidence_lower)

        # Decision
        passes = (
            overlap_ratio >= MIN_WORD_OVERLAP_RATIO
            or entity_overlap >= MIN_ENTITY_OVERLAP
            or phrase_matches > 0
            or len(claim.get("citations", [])) > 0  # Has explicit citations
        )

        claim_result = {
            **claim,
            "overlap_ratio": round(overlap_ratio, 3),
            "entity_overlap": entity_overlap,
            "phrase_matches": phrase_matches,
        }

        if passes:
            passed.append(claim_result)
        else:
            claim_result["reject_reason"] = (
                f"Low overlap: word={overlap_ratio:.2f}, "
                f"entity={entity_overlap}, phrases={phrase_matches}"
            )
            rejected.append(claim_result)

    logger.info(
        f"Lexical pre-filter: {len(passed)} passed, {len(rejected)} rejected "
        f"out of {len(claims)} claims"
    )

    return passed, rejected


def _tokenize(text: str) -> list[str]:
    """Simple word tokenizer — removes stopwords and short words."""
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "and", "but", "or",
        "nor", "not", "so", "than", "too", "very", "just", "about", "up",
        "it", "its", "this", "that", "these", "those", "such", "both",
        "each", "all", "any", "few", "more", "most", "other", "some",
    }
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    return [w for w in words if w not in stopwords]


def _extract_key_phrases(text: str) -> list[str]:
    """Extract potentially important multi-word phrases."""
    # Match capitalized phrases, quoted terms, and domain terms
    phrases = []

    # Capitalized multi-word phrases
    phrases.extend(re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', text))

    # Quoted terms
    phrases.extend(re.findall(r'"([^"]+)"', text))
    phrases.extend(re.findall(r"'([^']+)'", text))

    # Domain-specific patterns (Article N, Section N, Control-N, etc.)
    phrases.extend(re.findall(r'(?:Article|Section|Control|Clause|Annex)\s*[\w.-]+', text))

    return phrases
