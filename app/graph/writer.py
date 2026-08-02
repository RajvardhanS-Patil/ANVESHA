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

    # --- Phase 1: Generate embeddings in parallel ---
    embeddings: list[Optional[list[float]]] = [None] * len(entities)
    if generate_embeddings:
        import asyncio

        async def _embed_entity(idx: int, entity: dict) -> tuple[int, Optional[list[float]]]:
            embed_text = f"{entity['name']}: {entity.get('description', '')} {entity.get('exact_span', '')}"
            try:
                return idx, await router.embed(embed_text)
            except Exception as e:
                logger.warning(f"Embedding generation failed for {entity['name']}: {e}")
                return idx, None

        embed_tasks = [_embed_entity(i, e) for i, e in enumerate(entities)]
        embed_results = await asyncio.gather(*embed_tasks, return_exceptions=True)
        for result in embed_results:
            if isinstance(result, Exception):
                continue
            idx, emb = result
            embeddings[idx] = emb

    # --- Phase 2: Batch write all entities with UNWIND ---
    now = datetime.now(timezone.utc).isoformat()
    batch = []
    for i, entity in enumerate(entities):
        item = {
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
            "now": now,
            "embedding": embeddings[i],
        }
        batch.append(item)

    written = 0
    errors = 0

    try:
        query = """
        UNWIND $batch AS item
        MERGE (e:Entity {name: item.name, entity_type: item.entity_type})
        ON CREATE SET
            e.id = item.id,
            e.description = item.description,
            e.exact_span = item.exact_span,
            e.source_doc_id = item.source_doc_id,
            e.source_filename = item.source_filename,
            e.source_location = item.source_location,
            e.valid_from = item.valid_from,
            e.valid_to = item.valid_to,
            e.extraction_confidence = item.extraction_confidence,
            e.span_validated = item.span_validated,
            e.created_at = item.now
        ON MATCH SET
            e.description = CASE WHEN size(e.description) < size(item.description) THEN item.description ELSE e.description END,
            e.extraction_confidence = CASE WHEN item.extraction_confidence > e.extraction_confidence THEN item.extraction_confidence ELSE e.extraction_confidence END,
            e.updated_at = item.now
        WITH e, item
        WHERE item.embedding IS NOT NULL
        SET e.embedding = item.embedding
        RETURN count(e) AS written_count
        """

        result = await client.execute_write(query, {"batch": batch})
        written = result[0]["written_count"] if result else 0

    except Exception as e:
        logger.error(f"Batch entity write failed, falling back to individual writes: {e}")
        # Fallback: write one-by-one if batch fails
        for i, entity in enumerate(entities):
            try:
                single_query = """
                MERGE (e:Entity {name: $name, entity_type: $entity_type})
                ON CREATE SET
                    e.id = $id, e.description = $description,
                    e.exact_span = $exact_span, e.source_doc_id = $source_doc_id,
                    e.source_filename = $source_filename,
                    e.source_location = $source_location,
                    e.extraction_confidence = $extraction_confidence,
                    e.created_at = $now
                ON MATCH SET
                    e.updated_at = $now
                RETURN e.id AS id
                """
                params = {**batch[i]}
                params.pop("embedding", None)
                await client.execute_write(single_query, params)
                if embeddings[i]:
                    await client.execute_write(
                        "MATCH (e:Entity {name: $name, entity_type: $entity_type}) SET e.embedding = $embedding",
                        {"name": entity["name"], "entity_type": entity["entity_type"], "embedding": embeddings[i]}
                    )
                written += 1
            except Exception as e2:
                logger.error(f"Failed to write entity '{entity.get('name', '?')}': {e2}")
                errors += 1

    # --- Phase 3: Batch set entity type labels ---
    unique_types = set(e["entity_type"] for e in entities)
    for etype in unique_types:
        try:
            label_query = f"""
            MATCH (e:Entity {{entity_type: $entity_type}})
            WHERE NOT e:{etype}
            SET e:{etype}
            """
            await client.execute_write(label_query, {"entity_type": etype})
        except Exception:
            pass  # Label setting may fail if type has special chars

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


async def remediate_control_in_graph(requirement_id: str, code: str) -> dict:
    """
    Update a Control's status in Neo4j to 'MET' and connect it to a new
    Lyzr Auto-Remediation Evidence node.
    """
    client = get_neo4j_client()
    evidence_id = str(uuid.uuid4())
    evidence_name = "Lyzr Auto-Remediation Execution Record"
    evidence_desc = f"Compliance patch applied by Lyzr SecOps Agent. Code block:\n{code}"
    now = datetime.now(timezone.utc).isoformat()

    if not client.is_connected():
        logger.warning("Neo4j not connected — updating fallback in-memory cache")
        
        # 1. Update status of existing Control/Requirement in fallback cache
        control_node = None
        for node in _fallback_nodes:
            if node["id"] == requirement_id:
                node["status"] = "MET"
                node["description"] = (node.get("description") or "") + "\n(Remediated by Lyzr Agent)"
                control_node = node
                break

        # If not found in cache, create a mock control node for testing
        if not control_node:
            control_node = {
                "id": requirement_id,
                "name": "Remediated Control",
                "type": "Control",
                "status": "MET",
                "description": "Remediated by Lyzr Agent",
                "created_at": now
            }
            _fallback_nodes.append(control_node)

        # 2. Add Evidence node to fallback cache
        _fallback_nodes.append({
            "id": evidence_id,
            "name": evidence_name,
            "type": "Evidence",
            "description": evidence_desc,
            "source": "Lyzr Auto-Remediation Broker",
            "created_at": now
        })

        # 3. Add Edge connecting them
        _fallback_edges.append({
            "source": requirement_id,
            "target": evidence_id,
            "type": "EVIDENCED_BY",
            "description": "Lyzr automated remediation patch executed.",
            "created_at": now
        })

        return {"status": "success", "mode": "fallback", "evidence_id": evidence_id}

    # Neo4j connected: execute write query
    try:
        # Check if the node is in Neo4j and MERGE/SET its status to MET,
        # then create and link the Evidence node.
        query = """
        MATCH (c:Entity)
        WHERE c.id = $requirement_id OR c.name = $requirement_id
        SET c.status = 'MET'
        CREATE (e:Entity {
            id: $evidence_id,
            name: $evidence_name,
            entity_type: 'Evidence',
            description: $evidence_desc,
            source_filename: 'Lyzr Auto-Remediation Broker',
            extraction_confidence: 1.0,
            created_at: $created_at
        })
        CREATE (c)-[r:EVIDENCED_BY]->(e)
        RETURN c.id AS control_id, e.id AS evidence_id
        """
        result = await client.execute_write(query, {
            "requirement_id": requirement_id,
            "evidence_id": evidence_id,
            "evidence_name": evidence_name,
            "evidence_desc": evidence_desc,
            "created_at": now
        })
        logger.info(f"Compliance control {requirement_id} remediated in Neo4j graph: {result}")
        return {"status": "success", "mode": "neo4j", "evidence_id": evidence_id}
    except Exception as e:
        logger.error(f"Failed to write remediation to graph: {e}")
        return {"status": "error", "message": str(e)}

