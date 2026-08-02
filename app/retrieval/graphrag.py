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

    # --- Step 4.5: Corrective RAG — grade evidence and rewrite if weak ---
    corrective_rewrite = None
    if evidence_text and evidence_text != "No evidence found.":
        relevance_grade = await _grade_evidence_relevance(question, evidence_text, router)
        if relevance_grade < 0.5:
            logger.info(
                f"Evidence relevance low ({relevance_grade:.2f}), "
                f"attempting corrective query rewrite"
            )
            rewritten_question = await _rewrite_query(question, router)
            if rewritten_question and rewritten_question != question:
                corrective_rewrite = rewritten_question
                logger.info(f"Rewritten query: {rewritten_question[:100]}")
                # Re-retrieve with rewritten query
                try:
                    new_embedding = await router.embed(rewritten_question)
                    if client.is_connected():
                        new_seeds = await client.vector_search(
                            query_embedding=new_embedding,
                            top_k=top_k_seeds,
                            min_score=0.3,
                        )
                        if new_seeds:
                            new_seed_ids = [s["entity"]["id"] for s in new_seeds if "entity" in s]
                            if new_seed_ids:
                                new_subgraph = await client.k_hop_traversal(
                                    entity_ids=new_seed_ids,
                                    k_hops=k_hops,
                                    max_nodes=max_context_nodes,
                                )
                                new_evidence = _serialize_evidence(new_seeds, new_subgraph)
                                new_grade = await _grade_evidence_relevance(
                                    question, new_evidence, router
                                )
                                if new_grade > relevance_grade:
                                    logger.info(
                                        f"Corrective retrieval improved: "
                                        f"{relevance_grade:.2f} → {new_grade:.2f}"
                                    )
                                    seed_entities = new_seeds
                                    evidence_subgraph = new_subgraph
                                    evidence_text = new_evidence
                except Exception as e:
                    logger.warning(f"Corrective retrieval failed: {e}")

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
                "corrective_rewrite": corrective_rewrite,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store answer for evidence bundle retrieval
        _answer_store[answer_id] = result

        logger.info(
            f"GraphRAG answer [{answer_id[:8]}]: confidence={confidence}, "
            f"{len(citations)} citations, {elapsed:.2f}s"
            f"{' (corrective rewrite applied)' if corrective_rewrite else ''}"
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
    """
    Serialize seed entities and evidence subgraph into structured text for LLM.

    Uses reasoning-path format (inspired by knowledge_graph_rag_citations):
    each edge is rendered as a traversal chain with source provenance per hop,
    giving the LLM concrete paths for accurate citation generation.
    """
    parts = []

    # Build a node lookup for edge resolution
    nodes = subgraph.get("nodes", [])
    node_map: dict[str, dict] = {}
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            node_map[node["id"]] = node

    # Seed entities with scores and exact spans
    if seed_entities:
        parts.append("=== SEED ENTITIES (most relevant to query) ===")
        for s in seed_entities[:10]:
            entity = s.get("entity", {})
            score = s.get("score", 0)
            span = entity.get('exact_span', '')
            source_info = f"{entity.get('source_doc_id', 'N/A')} — {entity.get('source_location', 'N/A')}"
            parts.append(
                f"- {entity.get('name', '?')} ({entity.get('entity_type', '?')}) "
                f"[relevance: {score:.2f}]\n"
                f"  Description: {entity.get('description', 'N/A')}\n"
                f"  Source: {source_info}\n"
                f"  Verbatim: \"{span}\"" if span else ""
            )

    # Evidence subgraph nodes with verbatim spans
    if nodes:
        parts.append("\n=== EVIDENCE SUBGRAPH NODES ===")
        for node in nodes[:30]:
            if not isinstance(node, dict):
                continue
            span = node.get('exact_span', '')
            node_entry = (
                f"- {node.get('name', '?')} ({node.get('entity_type', '?')})\n"
                f"  Description: {node.get('description', 'N/A')}\n"
                f"  Source: {node.get('source_doc_id', 'N/A')} — {node.get('source_location', 'N/A')}"
            )
            if span:
                node_entry += f'\n  Verbatim: "{span}"'
            parts.append(node_entry)

    # Reasoning paths — structured traversal chains with provenance
    edges = subgraph.get("edges", [])
    if edges:
        parts.append("\n=== REASONING PATHS (entity → relationship → entity) ===")
        for edge in edges[:30]:
            if not isinstance(edge, dict):
                continue
            source_id = edge.get('source', '?')
            target_id = edge.get('target', '?')
            rel_type = edge.get('type', '?')

            # Resolve node names and sources from the lookup
            source_node = node_map.get(source_id, {})
            target_node = node_map.get(target_id, {})

            source_name = source_node.get('name', source_id)
            target_name = target_node.get('name', target_id)
            source_src = source_node.get('source_doc_id', '')
            target_src = target_node.get('source_doc_id', '')

            path_line = f"- {source_name} —[{rel_type}]→ {target_name}"

            # Add provenance per hop
            provenance_parts = []
            if source_src:
                provenance_parts.append(f"from: {source_src}")
            if target_src and target_src != source_src:
                provenance_parts.append(f"to: {target_src}")
            if provenance_parts:
                path_line += f"  ({', '.join(provenance_parts)})"

            # Edge description if available
            edge_props = edge.get('properties', {})
            if isinstance(edge_props, dict) and edge_props.get('description'):
                path_line += f"\n    Context: {edge_props['description']}"

            parts.append(path_line)

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


# --- Corrective RAG helpers (inspired by corrective_rag template) ---

RELEVANCE_GRADING_PROMPT = """You are a relevance grader. Given a compliance question and retrieved evidence,
rate how relevant the evidence is to answering the question.

QUESTION: {question}

EVIDENCE (first 2000 chars):
{evidence}

Respond with ONLY a JSON object: {{"relevance_score": 0.0 to 1.0, "reasoning": "brief explanation"}}

Score guide:
- 1.0: Evidence directly answers the question with specific facts
- 0.7: Evidence is clearly related and contains useful context
- 0.5: Evidence is tangentially related
- 0.3: Evidence mentions some terms but isn't really about the question
- 0.0: Evidence is completely unrelated"""


QUERY_REWRITE_PROMPT = """You are a compliance query optimizer. The original question retrieved poor evidence
from a knowledge graph of compliance documents.

Rewrite the question to be more specific and use compliance/regulatory terminology
that would match entity names and descriptions in a compliance knowledge graph.

ORIGINAL QUESTION: {question}

Rules:
- Use specific compliance terms (regulation names, control types, entity types)
- Break compound questions into the most important sub-question
- Keep the rewritten question concise (1-2 sentences max)
- Do NOT change the intent of the question

Respond with ONLY the rewritten question text, nothing else."""


async def _grade_evidence_relevance(
    question: str,
    evidence_text: str,
    router,
) -> float:
    """
    Grade how relevant the retrieved evidence is to the question.
    Returns a score from 0.0 (irrelevant) to 1.0 (perfectly relevant).

    Uses a cheap, fast LLM call with minimal tokens.
    """
    try:
        prompt = RELEVANCE_GRADING_PROMPT.format(
            question=question,
            evidence=evidence_text[:2000],
        )
        result = await router.generate_json(
            prompt=prompt,
            system_prompt="You are a strict relevance grader. Respond only with JSON.",
            provider=Provider.GROQ,
            temperature=0.0,
        )
        score = float(result.get("relevance_score", 0.5))
        logger.debug(f"Evidence relevance grade: {score:.2f} — {result.get('reasoning', '')[:80]}")
        return max(0.0, min(1.0, score))
    except Exception as e:
        logger.warning(f"Evidence grading failed: {e}")
        return 0.5  # Default: assume moderate relevance


async def _rewrite_query(question: str, router) -> str:
    """
    Rewrite a vague or ambiguous compliance question into a more specific form
    that will produce better vector search results against the knowledge graph.
    """
    try:
        rewritten = await router.generate(
            prompt=QUERY_REWRITE_PROMPT.format(question=question),
            system_prompt="You are a compliance query optimizer.",
            provider=Provider.GROQ,
            temperature=0.0,
            max_tokens=256,
        )
        rewritten = rewritten.strip().strip('"').strip("'")
        if rewritten and len(rewritten) > 10:
            return rewritten
        return question
    except Exception as e:
        logger.warning(f"Query rewrite failed: {e}")
        return question

