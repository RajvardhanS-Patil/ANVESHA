"""
ANVESHA Evidence Bundle — SHA-256 signed audit artifacts.

Produces a downloadable JSON bundle containing:
- The question and answer
- All evidence entities and relationships
- All citations with source references
- SHA-256 hash for tamper detection
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def create_evidence_bundle(answer_data: dict) -> dict:
    """
    Create a signed evidence bundle for an answer.

    The bundle contains everything needed to independently verify
    that the answer is grounded in the source evidence.

    Args:
        answer_data: Full answer dict from GraphRAG pipeline

    Returns:
        Evidence bundle with SHA-256 hash
    """
    bundle = {
        "bundle_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "answer_id": answer_data.get("answer_id", ""),

        # Question and answer
        "question": answer_data.get("question", ""),
        "answer": answer_data.get("answer", ""),
        "verified_answer": answer_data.get("verified_answer", answer_data.get("answer", "")),

        # Confidence and verification
        "confidence": answer_data.get("confidence", 0),
        "verification": answer_data.get("verification", {}),

        # Citations
        "citations": answer_data.get("citations", []),

        # Evidence subgraph
        "evidence": {
            "seed_entities": answer_data.get("evidence_subgraph", {}).get("seed_entities", []),
            "nodes": answer_data.get("evidence_subgraph", {}).get("nodes", []),
            "edges": answer_data.get("evidence_subgraph", {}).get("edges", []),
        },

        # Metadata
        "metadata": answer_data.get("metadata", {}),
    }

    # Compute SHA-256 hash of the evidence content
    # This allows later verification that the bundle hasn't been tampered with
    content_to_hash = json.dumps({
        "question": bundle["question"],
        "answer": bundle["answer"],
        "evidence": bundle["evidence"],
        "citations": bundle["citations"],
    }, sort_keys=True, ensure_ascii=True)

    bundle["integrity"] = {
        "algorithm": "SHA-256",
        "hash": hashlib.sha256(content_to_hash.encode()).hexdigest(),
        "scope": "question + answer + evidence + citations",
    }

    logger.info(
        f"Evidence bundle created: {bundle['answer_id'][:8]} "
        f"(hash: {bundle['integrity']['hash'][:16]}...)"
    )

    return bundle


def verify_bundle_integrity(bundle: dict) -> dict:
    """
    Verify that an evidence bundle hasn't been tampered with.

    Returns:
        Dict with 'valid' boolean and details
    """
    try:
        integrity = bundle.get("integrity", {})
        stored_hash = integrity.get("hash", "")

        if not stored_hash:
            return {"valid": False, "reason": "No integrity hash found"}

        # Recompute hash
        content_to_hash = json.dumps({
            "question": bundle.get("question", ""),
            "answer": bundle.get("answer", ""),
            "evidence": bundle.get("evidence", {}),
            "citations": bundle.get("citations", []),
        }, sort_keys=True, ensure_ascii=True)

        computed_hash = hashlib.sha256(content_to_hash.encode()).hexdigest()

        valid = computed_hash == stored_hash

        return {
            "valid": valid,
            "stored_hash": stored_hash,
            "computed_hash": computed_hash,
            "algorithm": integrity.get("algorithm", "SHA-256"),
            "reason": "Integrity verified" if valid else "Hash mismatch — bundle may have been tampered with",
        }

    except Exception as e:
        return {"valid": False, "reason": f"Verification error: {e}"}
