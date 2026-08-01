"""
ANVESHA Extractor — LLM-based entity/relationship extraction with span validation.

Uses Groq structured JSON output for ontology-constrained extraction.
Every extracted entity is span-validated against the source text.
"""

import logging
import time
import uuid
from typing import Optional
from datetime import datetime, timezone

from app.extraction.ontology import (
    normalize_entity_type,
    normalize_relation_type,
    get_extraction_system_prompt,
    get_extraction_user_prompt,
)
from app.ingestion.base import IngestedChunk
from app.providers.llm_router import get_llm_router, Provider

logger = logging.getLogger(__name__)


async def extract_entities_from_chunk(
    chunk: IngestedChunk,
    min_confidence: float = 0.5,
) -> dict:
    """
    Extract entities and relationships from a single ingested chunk.

    Args:
        chunk: IngestedChunk with text and provenance
        min_confidence: Minimum span-validation confidence to keep

    Returns:
        Dict with 'entities' and 'relationships' lists
    """
    text = chunk.cleaned_text or chunk.raw_text
    if not text or len(text.strip()) < 20:
        return {"entities": [], "relationships": []}

    source_location = chunk.citation
    router = get_llm_router()

    # Build prompts
    system_prompt = get_extraction_system_prompt()
    user_prompt = get_extraction_user_prompt(
        text=text,
        source_filename=chunk.source_filename,
        source_location=source_location,
    )

    try:
        # Call LLM for structured extraction
        result = await router.generate_json(
            prompt=user_prompt,
            system_prompt=system_prompt,
            provider=Provider.GROQ,
            temperature=0.0,
        )

        if "error" in result:
            logger.warning(f"Extraction failed for chunk {chunk.chunk_id[:8]}: {result.get('error')}")
            return {"entities": [], "relationships": []}

        # Process and validate entities
        raw_entities = result.get("entities", [])
        raw_relationships = result.get("relationships", [])

        validated_entities = []
        for ent in raw_entities:
            validated = _validate_entity(ent, text, chunk)
            if validated and validated.get("confidence", 0) >= min_confidence:
                validated_entities.append(validated)

        validated_relationships = []
        entity_names = {e["name"].lower() for e in validated_entities}
        for rel in raw_relationships:
            validated = _validate_relationship(rel, text, entity_names, chunk)
            if validated:
                validated_relationships.append(validated)

        logger.info(
            f"Extraction from chunk {chunk.chunk_id[:8]}: "
            f"{len(validated_entities)} entities, {len(validated_relationships)} relationships "
            f"(from {len(raw_entities)} raw entities, {len(raw_relationships)} raw relationships)"
        )

        return {
            "entities": validated_entities,
            "relationships": validated_relationships,
        }

    except Exception as e:
        logger.error(f"Entity extraction failed for chunk {chunk.chunk_id[:8]}: {e}")
        return {"entities": [], "relationships": []}


async def extract_entities_from_chunks(
    chunks: list[IngestedChunk],
    min_confidence: float = 0.5,
) -> dict:
    """
    Extract entities and relationships from multiple chunks.
    Deduplicates entities across chunks.

    Returns:
        Dict with 'entities', 'relationships', and 'stats'
    """
    start_time = time.perf_counter()
    all_entities = []
    all_relationships = []

    for i, chunk in enumerate(chunks):
        logger.info(f"Extracting from chunk {i+1}/{len(chunks)}: {chunk.chunk_id[:8]}")
        result = await extract_entities_from_chunk(chunk, min_confidence)
        all_entities.extend(result["entities"])
        all_relationships.extend(result["relationships"])

    # Deduplicate entities by name + type
    deduped_entities = _deduplicate_entities(all_entities)
    deduped_relationships = _deduplicate_relationships(all_relationships)

    elapsed = time.perf_counter() - start_time
    logger.info(
        f"Extraction complete: {len(deduped_entities)} unique entities, "
        f"{len(deduped_relationships)} unique relationships from {len(chunks)} chunks "
        f"in {elapsed:.2f}s"
    )

    return {
        "entities": deduped_entities,
        "relationships": deduped_relationships,
        "stats": {
            "total_chunks_processed": len(chunks),
            "raw_entities": len(all_entities),
            "raw_relationships": len(all_relationships),
            "unique_entities": len(deduped_entities),
            "unique_relationships": len(deduped_relationships),
            "processing_time_seconds": round(elapsed, 2),
        },
    }


def _validate_entity(entity: dict, source_text: str, chunk: IngestedChunk) -> Optional[dict]:
    """
    Validate and enrich an extracted entity.

    - Normalize entity type to ontology
    - Validate exact_span appears in source text
    - Compute confidence score based on span match
    """
    name = entity.get("name", "").strip()
    if not name:
        return None

    entity_type = normalize_entity_type(entity.get("entity_type", ""))
    description = entity.get("description", "").strip()
    exact_span = entity.get("exact_span", "").strip()

    # Span validation: check if exact_span is actually in source text
    confidence = 0.0
    span_validated = False

    if exact_span:
        if exact_span.lower() in source_text.lower():
            confidence = 1.0
            span_validated = True
        elif name.lower() in source_text.lower():
            # Name appears even if exact span doesn't match perfectly
            confidence = 0.7
            exact_span = _find_best_span(name, source_text)
            span_validated = True
        else:
            # Neither span nor name found — likely hallucinated
            confidence = 0.3
    elif name.lower() in source_text.lower():
        confidence = 0.7
        exact_span = _find_best_span(name, source_text)
        span_validated = True
    else:
        confidence = 0.2

    now = datetime.now(timezone.utc).isoformat()

    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "entity_type": entity_type,
        "description": description,
        "exact_span": exact_span,
        "span_validated": span_validated,
        "confidence": confidence,
        "source_doc_id": chunk.source_doc_id,
        "source_filename": chunk.source_filename,
        "source_location": chunk.source_location.to_dict() if chunk.source_location else None,
        "valid_from": now,
        "valid_to": None,
        "extraction_confidence": confidence,
    }


def _validate_relationship(
    rel: dict,
    source_text: str,
    entity_names: set,
    chunk: IngestedChunk,
) -> Optional[dict]:
    """Validate and enrich an extracted relationship."""
    source_entity = rel.get("source_entity", "").strip()
    target_entity = rel.get("target_entity", "").strip()
    relation_type = normalize_relation_type(rel.get("relation_type", ""))

    if not source_entity or not target_entity:
        return None

    # Check that both entities exist in our validated set
    source_found = source_entity.lower() in entity_names
    target_found = target_entity.lower() in entity_names

    if not source_found and not target_found:
        return None  # Both entities missing — skip

    description = rel.get("description", "").strip()
    exact_span = rel.get("exact_span", "").strip()

    return {
        "id": str(uuid.uuid4()),
        "source_entity": source_entity,
        "target_entity": target_entity,
        "relation_type": relation_type,
        "description": description,
        "exact_span": exact_span,
        "source_doc_id": chunk.source_doc_id,
        "source_filename": chunk.source_filename,
        "source_location": chunk.source_location.to_dict() if chunk.source_location else None,
    }


def _find_best_span(name: str, text: str) -> str:
    """Find the best matching span for an entity name in text."""
    # Find the name in text and return surrounding context
    lower_text = text.lower()
    lower_name = name.lower()
    idx = lower_text.find(lower_name)

    if idx == -1:
        return name

    # Get surrounding context (up to 100 chars each side)
    start = max(0, idx - 50)
    end = min(len(text), idx + len(name) + 50)

    # Expand to word boundaries
    while start > 0 and text[start] != " ":
        start -= 1
    while end < len(text) and text[end] != " ":
        end += 1

    return text[start:end].strip()


def _deduplicate_entities(entities: list[dict]) -> list[dict]:
    """Deduplicate entities by name + type, keeping highest confidence."""
    seen = {}
    for ent in entities:
        key = (ent["name"].lower(), ent["entity_type"])
        if key not in seen or ent.get("confidence", 0) > seen[key].get("confidence", 0):
            seen[key] = ent
    return list(seen.values())


def _deduplicate_relationships(relationships: list[dict]) -> list[dict]:
    """Deduplicate relationships by source + target + type."""
    seen = {}
    for rel in relationships:
        key = (
            rel["source_entity"].lower(),
            rel["target_entity"].lower(),
            rel["relation_type"],
        )
        if key not in seen:
            seen[key] = rel
    return list(seen.values())
