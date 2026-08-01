"""
ANVESHA GraphRAG — Graph Retrieval-Augmented Generation engine.

Pipeline:
1. Embed query → vector similarity search via Neo4j vector index → seed entities
2. k-hop Cypher traversal from seeds → evidence subgraph
3. Serialize subgraph as structured context for LLM
4. Generate draft answer with citations from evidence
"""

import logging
import time
import uuid
from typing import Optional
from datetime import datetime, timezone

from app.config import get_settings
from app.graph.neo4j_client import get_neo4j_client
from app.providers.llm_router import get_llm_router, Provider

logger = logging.getLogger(__name__)

# --- Answer store (in-memory for evidence bundle retrieval) ---
_answer_store: dict[str, dict] = {}


def get_answer_store() -> dict:
    """Get the in-memory answer store."""
    return _answer_store


GRAPHRAG_SYSTEM_PROMPT = """You are ANVESHA, an enterprise compliance intelligence assistant.
You answer compliance questions ONLY based on the evidence provided from a knowledge graph.

STRICT RULES:
1. ONLY use information from the provided evidence subgraph — never make up facts
2. For EVERY claim you make, include a citation in brackets like [Source: filename — Page N] or [Source: filename — Timestamp MM:SS]
3. If the evidence does NOT contain enough information to answer the question, explicitly say "I cannot answer this based on the available evidence" — DO NOT fabricate an answer
4. Structure your answer clearly with headers if the question is complex
5. At the end, list all sources used under a "Sources" section
6. Rate your confidence from 0-100 based on how well the evidence supports your answer"""

GRAPHRAG_USER_PROMPT = """Answer the following compliance question using ONLY the evidence subgraph below.

QUESTION: {question}

EVIDENCE SUBGRAPH:
{evidence}

Remember:
- Cite every claim with [Source: filename — location]
- If insufficient evidence, say so explicitly
- Include a confidence score (0-100)
- List all sources at the end"""


async def query_graphrag(
    question: str,
    k_hops: Optional[int] = None,
    top_k_seeds: Optional[int] = None,
    max_context_nodes: Optional[int] = None,
    as_of: Optional[str] = None,
) -> dict:
    """
    Full GraphRAG pipeline: query → retrieve → generate answer.

    Args:
        question: The compliance question to answer
        k_hops: Number of graph traversal hops (default from config)
        top_k_seeds: Number of seed entities from vector search
        max_context_nodes: Maximum nodes in evidence context
        as_of: Optional date string to query historical status

    Returns:
        Dict with answer, citations, confidence, evidence subgraph, answer_id
    """
    settings = get_settings()
    k_hops = k_hops or settings.graphrag_k_hops
    top_k_seeds = top_k_seeds or settings.graphrag_top_k_seeds
    max_context_nodes = max_context_nodes or settings.graphrag_max_context_nodes

    start_time = time.perf_counter()
    answer_id = str(uuid.uuid4())

    logger.info(f"GraphRAG query [{answer_id[:8]}]: {question[:100]} (as_of={as_of})")

    router = get_llm_router()
    client = get_neo4j_client()

    # --- Step 1: Embed the query ---
    try:
        query_embedding = await router.embed(question)
    except Exception as e:
        logger.error(f"Query embedding failed: {e}")
        return _error_response(answer_id, question, f"Embedding failed: {e}", start_time)

    # --- Step 2: Vector search for seed entities ---
    seed_entities = []
    if client.is_connected():
        try:
            seed_results = await client.vector_search(
                query_embedding=query_embedding,
                top_k=top_k_seeds,
                min_score=0.3,
            )
            seed_entities = seed_results
            logger.info(f"Vector search found {len(seed_entities)} seed entities")
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

    # --- Step 3: k-hop traversal for evidence subgraph ---
    evidence_subgraph = {"nodes": [], "edges": []}
    if seed_entities and client.is_connected():
        try:
            seed_ids = [s["entity"]["id"] for s in seed_entities if "entity" in s]
            if seed_ids:
                evidence_subgraph = await client.k_hop_traversal(
                    entity_ids=seed_ids,
                    k_hops=k_hops,
                    max_nodes=max_context_nodes,
                )
                logger.info(
                    f"k-hop traversal: {len(evidence_subgraph.get('nodes', []))} nodes, "
                    f"{len(evidence_subgraph.get('edges', []))} edges"
                )
        except Exception as e:
            logger.warning(f"k-hop traversal failed: {e}")

    # --- Apply Temporal Compliance (Time-Travel) Filtering ---
    if as_of:
        # Filter seed entities
        seed_entities = [
            s for s in seed_entities
            if not s.get("entity", {}).get("created_at") or s.get("entity", {}).get("created_at") <= as_of
        ]
        
        # Filter subgraph nodes
        filtered_nodes = [
            n for n in evidence_subgraph.get("nodes", [])
            if not n.get("created_at") or n.get("created_at") <= as_of
        ]
        filtered_node_ids = {n["id"] for n in filtered_nodes}
        
        # Filter subgraph edges
        filtered_edges = [
            e for e in evidence_subgraph.get("edges", [])
            if (not e.get("created_at") or e.get("created_at") <= as_of)
            and e.get("source") in filtered_node_ids
            and e.get("target") in filtered_node_ids
        ]
        
        evidence_subgraph = {
            "nodes": filtered_nodes,
            "edges": filtered_edges
        }
        logger.info(f"Temporal filtered: {len(filtered_nodes)} nodes, {len(filtered_edges)} edges")

    # --- Step 4: Serialize evidence for LLM context ---
    evidence_text = _serialize_evidence(seed_entities, evidence_subgraph)

    if not evidence_text.strip() or evidence_text == "No evidence found.":
        # No evidence — try text-based fallback from chunk store
        evidence_text = await _fallback_text_search(question)

    # --- Step 5: Generate answer ---
    try:
        user_prompt = GRAPHRAG_USER_PROMPT.format(
            question=question,
            evidence=evidence_text,
        )

        raw_answer = await router.generate(
            prompt=user_prompt,
            system_prompt=GRAPHRAG_SYSTEM_PROMPT,
            provider=Provider.GROQ,
            temperature=0.1,
            max_tokens=2048,
        )

        # Parse confidence from the answer
        confidence = _extract_confidence(raw_answer)

        # Extract citations
        citations = _extract_citations(raw_answer)

        elapsed = time.perf_counter() - start_time

        result = {
            "answer_id": answer_id,
            "question": question,
            "answer": raw_answer,
            "confidence": confidence,
            "citations": citations,
            "evidence_subgraph": {
                "seed_entities": [
                    {
                        "name": s.get("entity", {}).get("name", ""),
                        "type": s.get("entity", {}).get("entity_type", ""),
                        "score": round(s.get("score", 0), 3),
                    }
                    for s in seed_entities[:10]
                ],
                "nodes": evidence_subgraph.get("nodes", [])[:max_context_nodes],
                "edges": evidence_subgraph.get("edges", []),
            },
            "metadata": {
                "k_hops": k_hops,
                "top_k_seeds": top_k_seeds,
                "seed_count": len(seed_entities),
                "evidence_nodes": len(evidence_subgraph.get("nodes", [])),
                "evidence_edges": len(evidence_subgraph.get("edges", [])),
                "processing_time_seconds": round(elapsed, 2),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store answer for evidence bundle retrieval
        _answer_store[answer_id] = result

        logger.info(
            f"GraphRAG answer [{answer_id[:8]}]: confidence={confidence}, "
            f"{len(citations)} citations, {elapsed:.2f}s"
        )

        return result

    except Exception as e:
        logger.error(f"Answer generation failed: {e}")
        return _error_response(answer_id, question, f"Generation failed: {e}", start_time)


async def _fallback_text_search(question: str) -> str:
    """
    Fallback: search in-memory chunk store when Neo4j is unavailable.
    Uses simple keyword matching.
    """
    from app.ingestion.router import get_chunk_store

    chunk_store = get_chunk_store()
    if not chunk_store:
        return "No evidence found."

    # Simple keyword search
    question_words = set(question.lower().split())
    scored_chunks = []

    for chunk_id, chunk_data in chunk_store.items():
        text = chunk_data.get("raw_text", "").lower()
        if not text:
            continue

        # Count keyword matches
        matches = sum(1 for w in question_words if w in text and len(w) > 3)
        if matches > 0:
            scored_chunks.append((matches, chunk_data))

    # Sort by relevance and take top chunks
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    top_chunks = scored_chunks[:5]

    if not top_chunks:
        return "No evidence found."

    evidence_parts = []
    for score, chunk in top_chunks:
        source = chunk.get("source_filename", "unknown")
        loc = chunk.get("source_location", {})
        loc_str = f"Page {loc.get('page', '?')}" if loc and loc.get("page") else "unknown location"
        text = chunk.get("raw_text", "")[:500]
        evidence_parts.append(f"[Source: {source} — {loc_str}]\n{text}")

    return "\n\n---\n\n".join(evidence_parts)


def _serialize_evidence(seed_entities: list, subgraph: dict) -> str:
    """Serialize seed entities and evidence subgraph into text for LLM."""
    parts = []

    # Seed entities with scores
    if seed_entities:
        parts.append("=== SEED ENTITIES (most relevant to query) ===")
        for s in seed_entities[:10]:
            entity = s.get("entity", {})
            score = s.get("score", 0)
            parts.append(
                f"- {entity.get('name', '?')} ({entity.get('entity_type', '?')}) "
                f"[relevance: {score:.2f}]\n"
                f"  Description: {entity.get('description', 'N/A')}\n"
                f"  Source: {entity.get('source_doc_id', 'N/A')} — {entity.get('source_location', 'N/A')}\n"
                f"  Span: \"{entity.get('exact_span', 'N/A')}\""
            )

    # Subgraph nodes
    nodes = subgraph.get("nodes", [])
    if nodes:
        parts.append("\n=== EVIDENCE SUBGRAPH NODES ===")
        for node in nodes[:30]:
            parts.append(
                f"- {node.get('name', '?')} ({node.get('entity_type', '?')})\n"
                f"  Description: {node.get('description', 'N/A')}\n"
                f"  Source: {node.get('source_doc_id', 'N/A')} — {node.get('source_location', 'N/A')}"
            )

    # Subgraph edges
    edges = subgraph.get("edges", [])
    if edges:
        parts.append("\n=== RELATIONSHIPS ===")
        for edge in edges[:30]:
            parts.append(
                f"- {edge.get('source', '?')} —[{edge.get('type', '?')}]→ {edge.get('target', '?')}"
            )

    return "\n".join(parts) if parts else "No evidence found."


def _extract_confidence(answer: str) -> int:
    """Extract confidence score from the answer text."""
    import re
    # Look for patterns like "Confidence: 85", "confidence score: 90/100"
    patterns = [
        r"[Cc]onfidence[:\s]*(\d{1,3})",
        r"[Cc]onfidence\s*score[:\s]*(\d{1,3})",
        r"(\d{1,3})/100",
        r"(\d{1,3})%",
    ]
    for pattern in patterns:
        match = re.search(pattern, answer)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 100:
                return val
    return 50  # Default moderate confidence


def _extract_citations(answer: str) -> list[str]:
    """Extract citation references from the answer text."""
    import re
    # Match [Source: ...] patterns
    citations = re.findall(r"\[Source:([^\]]+)\]", answer)
    # Also match [filename — Page N] patterns
    citations += re.findall(r"\[([^\]]*(?:Page|Timestamp|Section)[^\]]*)\]", answer)
    # Deduplicate
    seen = set()
    unique = []
    for c in citations:
        c = c.strip()
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _error_response(answer_id: str, question: str, error: str, start_time: float) -> dict:
    """Build an error response."""
    return {
        "answer_id": answer_id,
        "question": question,
        "answer": f"I was unable to process this query. Error: {error}",
        "confidence": 0,
        "citations": [],
        "evidence_subgraph": {"seed_entities": [], "nodes": [], "edges": []},
        "metadata": {
            "error": error,
            "processing_time_seconds": round(time.perf_counter() - start_time, 2),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
