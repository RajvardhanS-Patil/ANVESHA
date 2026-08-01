"""
ANVESHA Graph Writer — Write extracted entities and relationships to Neo4j.

Handles:
- MERGE entities with versioning (valid_from/valid_to)
- MERGE relationships with provenance
- Embedding generation and storage
- Document node creation
"""

import logging
import time
import uuid
from typing import Optional
from datetime import datetime, timezone

from app.graph.neo4j_client import get_neo4j_client
from app.providers.llm_router import get_llm_router
from app.ingestion.base import IngestedChunk, IngestionResult

logger = logging.getLogger(__name__)

# --- Fallback In-Memory Cache (for offline mode) ---
_fallback_nodes: list[dict] = []
_fallback_edges: list[dict] = []


async def write_document_to_graph(ingestion_result: IngestionResult) -> dict:
    """
    Write an ingested document node to the knowledge graph.

    Returns:
        Dict with write stats
    """
    client = get_neo4j_client()

    if not client.is_connected():
        logger.warning("Neo4j not connected — skipping document write")
        return {"status": "skipped", "reason": "neo4j_disconnected"}

    query = """
    MERGE (d:Document {id: $doc_id})
    SET d.filename = $filename,
        d.content_type = $content_type,
        d.total_chunks = $total_chunks,
        d.ingested_at = $ingested_at,
        d.status = $status
    RETURN d.id AS id
    """

    now = datetime.now(timezone.utc).isoformat()
    await client.execute_write(query, {
        "doc_id": ingestion_result.doc_id,
        "filename": ingestion_result.filename,
        "content_type": ingestion_result.content_type,
        "total_chunks": ingestion_result.total_chunks,
        "ingested_at": now,
        "status": ingestion_result.status,
    })

    logger.info(f"Document node written: {ingestion_result.doc_id} ({ingestion_result.filename})")
    return {"status": "written", "doc_id": ingestion_result.doc_id}


async def write_entities_to_graph(
    entities: list[dict],
    generate_embeddings: bool = True,
) -> dict:
    """
    Write extracted entities to Neo4j.

    - MERGE by entity name + type (avoid duplicates)
    - Set provenance fields
    - Generate and store embeddings
    """
    client = get_neo4j_client()
    if not client.is_connected():
        logger.warning("Neo4j not connected — caching entities in-memory")
        written = 0
        for e in entities:
            exists = any(fn["name"].lower() == e["name"].lower() and fn["type"] == e["entity_type"] for fn in _fallback_nodes)
            if not exists:
                _fallback_nodes.append({
                    "id": e.get("id") or str(uuid.uuid4()),
                    "name": e["name"],
                    "type": e["entity_type"],
                    "description": e.get("description", ""),
                    "confidence": e.get("extraction_confidence", 0.8),
                    "source": e.get("source_filename", "Custom Curation"),
                    "created_at": datetime.now(timezone.utc).isoformat()
                })
                written += 1
        return {
            "status": "success",
            "written": written,
            "errors": 0,
            "total": len(entities),
            "time_seconds": 0.0,
        }

    router = get_llm_router()
    start_time = time.perf_counter()
    written = 0
    errors = 0

    for entity in entities:
        try:
            # Generate embedding for the entity
            embedding = None
            if generate_embeddings:
                embed_text = f"{entity['name']}: {entity.get('description', '')} {entity.get('exact_span', '')}"
                try:
                    embedding = await router.embed(embed_text)
                except Exception as e:
                    logger.warning(f"Embedding generation failed for {entity['name']}: {e}")

            # Write entity node
            query = """
            MERGE (e:Entity {name: $name, entity_type: $entity_type})
            ON CREATE SET
                e.id = $id,
                e.description = $description,
                e.exact_span = $exact_span,
                e.source_doc_id = $source_doc_id,
                e.source_filename = $source_filename,
                e.source_location = $source_location,
                e.valid_from = $valid_from,
                e.valid_to = $valid_to,
                e.extraction_confidence = $extraction_confidence,
                e.span_validated = $span_validated,
                e.created_at = $now
            ON MATCH SET
                e.description = CASE WHEN size(e.description) < size($description) THEN $description ELSE e.description END,
                e.extraction_confidence = CASE WHEN $extraction_confidence > e.extraction_confidence THEN $extraction_confidence ELSE e.extraction_confidence END,
                e.updated_at = $now
            """
            params = {
                "id": entity["id"],
                "name": entity["name"],
                "entity_type": entity["entity_type"],
                "description": entity.get("description", ""),
                "exact_span": entity.get("exact_span", ""),
                "source_doc_id": entity.get("source_doc_id", ""),
                "source_filename": entity.get("source_filename", ""),
                "source_location": str(entity.get("source_location", "")),
                "valid_from": entity.get("valid_from", ""),
                "valid_to": entity.get("valid_to"),
                "extraction_confidence": entity.get("extraction_confidence", 0.5),
                "span_validated": entity.get("span_validated", False),
                "now": datetime.now(timezone.utc).isoformat(),
            }

            # Add embedding if available
            if embedding:
                query += "\nSET e.embedding = $embedding"
                params["embedding"] = embedding

            query += "\nRETURN e.id AS id"

            await client.execute_write(query, params)

            # Add entity type as label
            label_query = f"""
            MATCH (e:Entity {{name: $name, entity_type: $entity_type}})
            SET e:{entity['entity_type']}
            """
            try:
                await client.execute_write(label_query, {
                    "name": entity["name"],
                    "entity_type": entity["entity_type"],
                })
            except Exception:
                pass  # Label setting may fail if type has special chars

            written += 1

        except Exception as e:
            logger.error(f"Failed to write entity '{entity.get('name', '?')}': {e}")
            errors += 1

    elapsed = time.perf_counter() - start_time
    logger.info(f"Entities written: {written}/{len(entities)} in {elapsed:.2f}s ({errors} errors)")

    return {
        "status": "success",
        "written": written,
        "errors": errors,
        "total": len(entities),
        "time_seconds": round(elapsed, 2),
    }


async def write_relationships_to_graph(relationships: list[dict]) -> dict:
    """
    Write extracted relationships to Neo4j.

    - Creates edges between existing entity nodes
    - Sets provenance fields on edges
    """
    client = get_neo4j_client()
    if not client.is_connected():
        logger.warning("Neo4j not connected — caching relationships in-memory")
        written = 0
        for r in relationships:
            source_node = next((n for n in _fallback_nodes if n["name"].lower() == r["source_entity"].lower()), None)
            target_node = next((n for n in _fallback_nodes if n["name"].lower() == r["target_entity"].lower()), None)
            
            source_id = source_node["id"] if source_node else r["source_entity"].lower().replace(" ", "-")
            target_id = target_node["id"] if target_node else r["target_entity"].lower().replace(" ", "-")
            
            exists = any(rel["source"] == source_id and rel["target"] == target_id and rel["type"] == r["relation_type"] for rel in _fallback_edges)
            if not exists:
                _fallback_edges.append({
                    "source": source_id,
                    "target": target_id,
                    "type": r["relation_type"],
                    "description": r.get("description", ""),
                    "created_at": datetime.now(timezone.utc).isoformat()
                })
                written += 1
        return {"status": "success", "written": written, "errors": 0, "total": len(relationships)}

    start_time = time.perf_counter()
    written = 0
    errors = 0

    for rel in relationships:
        try:
            # Use APOC to create dynamic relationship types
            # Fallback to RELATED_TO if APOC isn't available
            rel_type = rel["relation_type"]

            query = f"""
            MATCH (source:Entity)
            WHERE toLower(source.name) = toLower($source_name)
            MATCH (target:Entity)
            WHERE toLower(target.name) = toLower($target_name)
            MERGE (source)-[r:{rel_type}]->(target)
            SET r.id = $id,
                r.description = $description,
                r.exact_span = $exact_span,
                r.source_doc_id = $source_doc_id,
                r.source_filename = $source_filename,
                r.source_location = $source_location,
                r.created_at = $now
            RETURN r.id AS id
            """

            result = await client.execute_write(query, {
                "source_name": rel["source_entity"],
                "target_name": rel["target_entity"],
                "id": rel["id"],
                "description": rel.get("description", ""),
                "exact_span": rel.get("exact_span", ""),
                "source_doc_id": rel.get("source_doc_id", ""),
                "source_filename": rel.get("source_filename", ""),
                "source_location": str(rel.get("source_location", "")),
                "now": datetime.now(timezone.utc).isoformat(),
            })

            if result:
                written += 1
            else:
                logger.debug(
                    f"Relationship not written (entities not found): "
                    f"{rel['source_entity']} -[{rel_type}]-> {rel['target_entity']}"
                )

        except Exception as e:
            logger.error(
                f"Failed to write relationship "
                f"'{rel.get('source_entity', '?')}' -> '{rel.get('target_entity', '?')}': {e}"
            )
            errors += 1

    elapsed = time.perf_counter() - start_time
    logger.info(f"Relationships written: {written}/{len(relationships)} in {elapsed:.2f}s ({errors} errors)")

    return {
        "status": "success",
        "written": written,
        "errors": errors,
        "total": len(relationships),
        "time_seconds": round(elapsed, 2),
    }


async def write_mention_edges(entities: list[dict]) -> dict:
    """
    Create MENTIONED_IN edges between entities and their source documents.
    """
    client = get_neo4j_client()
    if not client.is_connected():
        return {"status": "skipped", "written": 0}

    written = 0
    for entity in entities:
        doc_id = entity.get("source_doc_id")
        if not doc_id:
            continue

        try:
            query = """
            MATCH (e:Entity {name: $entity_name, entity_type: $entity_type})
            MATCH (d:Document {id: $doc_id})
            MERGE (e)-[r:MENTIONED_IN]->(d)
            SET r.source_location = $source_location,
                r.exact_span = $exact_span
            RETURN r
            """
            result = await client.execute_write(query, {
                "entity_name": entity["name"],
                "entity_type": entity["entity_type"],
                "doc_id": doc_id,
                "source_location": str(entity.get("source_location", "")),
                "exact_span": entity.get("exact_span", ""),
            })
            if result:
                written += 1
        except Exception as e:
            logger.debug(f"MENTIONED_IN edge failed: {e}")

    return {"status": "success", "written": written}


async def get_full_graph(as_of: Optional[str] = None) -> dict:
    """
    Get the full knowledge graph for visualization.

    Returns:
        Dict with 'nodes' and 'edges' arrays
    """
    client = get_neo4j_client()
    if not client.is_connected():
        if as_of:
            filtered_nodes = [n for n in _fallback_nodes if n.get("created_at", "") <= as_of]
            filtered_node_ids = {n["id"] for n in filtered_nodes}
            filtered_edges = [
                e for e in _fallback_edges
                if e.get("created_at", "") <= as_of
                and e["source"] in filtered_node_ids
                and e["target"] in filtered_node_ids
            ]
            return {"nodes": filtered_nodes, "edges": filtered_edges}
        return {
            "nodes": _fallback_nodes,
            "edges": _fallback_edges,
        }

    try:
        # Get all entities
        nodes_query = """
        MATCH (e:Entity)
        WHERE $as_of IS NULL OR e.created_at <= $as_of OR e.valid_from <= $as_of
        RETURN e.id AS id, e.name AS name, e.entity_type AS type,
               e.description AS description, e.extraction_confidence AS confidence,
               e.source_filename AS source, e.created_at AS created_at
        ORDER BY e.name
        """
        nodes = await client.execute_read(nodes_query, {"as_of": as_of})

        # Get all relationships
        edges_query = """
        MATCH (a:Entity)-[r]->(b:Entity)
        WHERE $as_of IS NULL OR r.created_at <= $as_of OR (
            (a.created_at IS NULL OR a.created_at <= $as_of) AND
            (b.created_at IS NULL OR b.created_at <= $as_of)
        )
        RETURN a.id AS source, b.id AS target, type(r) AS type,
               r.description AS description
        """
        edges = await client.execute_read(edges_query, {"as_of": as_of})

        return {
            "nodes": nodes,
            "edges": edges,
        }
    except Exception as e:
        logger.error(f"Failed to get full graph: {e}")
        return {"nodes": [], "edges": []}
