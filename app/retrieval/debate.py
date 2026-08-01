"""
ANVESHA Multi-Agent Compliance Debate Engine.

Orchestrates a 3-agent debate (Advocate vs Skeptic with Adjudicator Judge)
to analyze complex or ambiguous compliance questions against evidence.
"""

import logging
import time
import uuid
from typing import Optional
from datetime import datetime, timezone

from app.config import get_settings
from app.graph.neo4j_client import get_neo4j_client
from app.providers.llm_router import get_llm_router, Provider
from app.retrieval.graphrag import _serialize_evidence, _fallback_text_search, _extract_citations

logger = logging.getLogger(__name__)

# --- In-memory store (reused from standard graphrag) ---
from app.retrieval.graphrag import get_answer_store

ADVOCATE_SYSTEM_PROMPT = """You are the compliance ADVOCATE agent for ANVESHA.
Your job is to make the strongest possible argument that the company's systems, policies,
and evidence COMPLY with the regulatory requirement or answer the question affirmatively.

RULES:
1. Rely ONLY on the provided evidence subgraph.
2. Interpret the facts and requirements in the best possible light for the company.
3. Call out every control, policy, or system that satisfies the requirement.
4. For every claim, cite the source in brackets: [Source: filename — location].
5. Keep your argument concise, professional, and structured.
"""

SKEPTIC_SYSTEM_PROMPT = """You are the compliance SKEPTIC agent for ANVESHA.
Your job is to point out gaps, risks, vulnerabilities, and counter-arguments showing why
the company's systems, policies, or evidence might NOT fully comply, or why the answer is not simple.

RULES:
1. Analyze the compliance question, the evidence, and the Advocate's argument.
2. Find loopholes, missing sub-controls, lack of specific implementation details, or potential risks.
3. Rely ONLY on the evidence subgraph — do not fabricate non-compliance details, but critique the lack of proof.
4. For every claim, cite the source in brackets: [Source: filename — location].
5. Keep your argument concise, analytical, and professional.
"""

JUDGE_SYSTEM_PROMPT = """You are the compliance JUDGE (Adjudicator) agent for ANVESHA.
Your job is to objectively evaluate the Advocate's and Skeptic's arguments against the provided evidence subgraph.
You will make the final compliance ruling.

RULES:
1. Be impartial and base your verdict strictly on the evidence and the strengths of both arguments.
2. Synthesize a balanced verdict explaining the compliance status.
3. You MUST end your response with a final ruling block exactly like this:
   RULING: [MET / PARTIAL / GAP]
   CONFIDENCE: [0-100]
4. Rate compliance as:
   - MET: Complete, robust evidence of compliance.
   - PARTIAL: Some controls exist but gaps/risks remain.
   - GAP: Major controls are missing or evidence is absent.
5. Provide a list of citations for the evidence you relied upon.
"""

DEBATE_USER_PROMPT = """Analyze the compliance question using the evidence subgraph below.

QUESTION: {question}

EVIDENCE SUBGRAPH:
{evidence}
"""


async def run_compliance_debate(
    question: str,
    k_hops: Optional[int] = None,
    top_k_seeds: Optional[int] = None,
    max_context_nodes: Optional[int] = None,
    as_of: Optional[str] = None,
) -> dict:
    """
    Run the multi-agent debate loop:
    1. Retrieve evidence subgraph (GraphRAG).
    2. Invoke Advocate (Llama-3/Groq).
    3. Invoke Skeptic (Llama-3/Groq), passing the Advocate's response.
    4. Invoke Judge (Gemini), passing both arguments to make the final ruling.
    """
    settings = get_settings()
    k_hops = k_hops or settings.graphrag_k_hops
    top_k_seeds = top_k_seeds or settings.graphrag_top_k_seeds
    max_context_nodes = max_context_nodes or settings.graphrag_max_context_nodes

    start_time = time.perf_counter()
    answer_id = str(uuid.uuid4())

    logger.info(f"Initiating Compliance Debate [{answer_id[:8]}]: {question[:100]} (as_of={as_of})")

    router = get_llm_router()
    client = get_neo4j_client()

    # --- Step 1: Retrieve Evidence (GraphRAG) ---
    evidence_text = ""
    seed_entities = []
    evidence_subgraph = {"nodes": [], "edges": []}

    try:
        query_embedding = await router.embed(question)
        if client.is_connected():
            seed_results = await client.vector_search(query_embedding, top_k=top_k_seeds, min_score=0.3)
            seed_entities = seed_results
            seed_ids = [s["entity"]["id"] for s in seed_entities if "entity" in s]
            if seed_ids:
                evidence_subgraph = await client.k_hop_traversal(
                    entity_ids=seed_ids,
                    k_hops=k_hops,
                    max_nodes=max_context_nodes
                )
        
        # Apply Temporal Compliance (Time-Travel) Filtering
        if as_of:
            seed_entities = [
                s for s in seed_entities
                if not s.get("entity", {}).get("created_at") or s.get("entity", {}).get("created_at") <= as_of
            ]
            filtered_nodes = [
                n for n in evidence_subgraph.get("nodes", [])
                if not n.get("created_at") or n.get("created_at") <= as_of
            ]
            filtered_node_ids = {n["id"] for n in filtered_nodes}
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

        evidence_text = _serialize_evidence(seed_entities, evidence_subgraph)
    except Exception as e:
        logger.error(f"Debate context retrieval failed: {e}")

    # Fallback to local text search if no graph matches
    if not evidence_text.strip() or evidence_text == "No evidence found.":
        evidence_text = await _fallback_text_search(question)

    # --- Step 2: Agent 1 - Advocate Argument ---
    logger.info(f"[{answer_id[:8]}] Invoking Advocate agent...")
    try:
        advocate_prompt = DEBATE_USER_PROMPT.format(question=question, evidence=evidence_text)
        advocate_arg = await router.generate(
            prompt=advocate_prompt,
            system_prompt=ADVOCATE_SYSTEM_PROMPT,
            provider=Provider.GROQ, # Groq for speed
            temperature=0.1,
            max_tokens=1024
        )
    except Exception as e:
        logger.error(f"Advocate failed: {e}")
        advocate_arg = f"Advocate agent failed to formulate an argument due to error: {e}"

    # --- Step 3: Agent 2 - Skeptic Argument ---
    logger.info(f"[{answer_id[:8]}] Invoking Skeptic agent...")
    try:
        skeptic_prompt = (
            f"{DEBATE_USER_PROMPT.format(question=question, evidence=evidence_text)}\n\n"
            f"ADVOCATE ARGUMENT FOR COMPLIANCE:\n{advocate_arg}"
        )
        skeptic_arg = await router.generate(
            prompt=skeptic_prompt,
            system_prompt=SKEPTIC_SYSTEM_PROMPT,
            provider=Provider.GROQ,
            temperature=0.1,
            max_tokens=1024
        )
    except Exception as e:
        logger.error(f"Skeptic failed: {e}")
        skeptic_arg = f"Skeptic agent failed to formulate an argument due to error: {e}"

    # --- Step 4: Agent 3 - Adjudicator Judge Ruling ---
    logger.info(f"[{answer_id[:8]}] Invoking Adjudicator Judge agent...")
    try:
        judge_prompt = (
            f"EVIDENCE SUBGRAPH:\n{evidence_text}\n\n"
            f"ADVOCATE COMPLIANCE ARGUMENT:\n{advocate_arg}\n\n"
            f"SKEPTIC COUNTER-ARGUMENT:\n{skeptic_arg}\n\n"
            f"Please review the question: '{question}' and evaluate compliance based strictly on the above."
        )
        judge_ruling = await router.generate(
            prompt=judge_prompt,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            provider=Provider.GEMINI, # Gemini for deep analysis/objectivity
            temperature=0.0,
            max_tokens=1536
        )
    except Exception as e:
        logger.error(f"Judge failed: {e}")
        judge_ruling = f"Adjudicator failed to formulate ruling due to error: {e}\n\nRULING: GAP\nCONFIDENCE: 0"

    # --- Step 5: Post-Process and Parse Decision ---
    verdict, confidence = _parse_judge_ruling(judge_ruling)
    citations = _extract_citations(advocate_arg) + _extract_citations(skeptic_arg) + _extract_citations(judge_ruling)
    # Deduplicate citations
    citations = list(set(citations))

    elapsed = time.perf_counter() - start_time

    # Compile result
    result = {
        "answer_id": answer_id,
        "question": question,
        "debate_mode": True,
        "verdict": verdict,
        "confidence": confidence,
        "answer": judge_ruling,  # The Judge's synthesized ruling is the final answer
        "advocate_argument": advocate_arg,
        "skeptic_argument": skeptic_arg,
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
            "evidence_nodes": len(evidence_subgraph.get("nodes", [])),
            "processing_time_seconds": round(elapsed, 2),
            "agents_involved": ["Advocate", "Skeptic", "Judge"]
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store answer in-memory for evidence bundle retrieval
    answer_store = get_answer_store()
    answer_store[answer_id] = result

    logger.info(f"Debate completed in {elapsed:.2f}s: Verdict={verdict}, Confidence={confidence}%")
    return result


def _parse_judge_ruling(ruling: str) -> tuple[str, int]:
    """Parse compliance verdict and confidence from Judge's output."""
    import re
    verdict = "PARTIAL"
    confidence = 50

    # Look for RULING: MET/PARTIAL/GAP
    ruling_match = re.search(r"RULING[:\s]*([M|P|G]\w+)", ruling, re.IGNORECASE)
    if ruling_match:
        val = ruling_match.group(1).upper()
        if val in ["MET", "PARTIAL", "GAP"]:
            verdict = val

    # Look for CONFIDENCE: X
    conf_match = re.search(r"CONFIDENCE[:\s]*(\d+)", ruling, re.IGNORECASE)
    if conf_match:
        try:
            val = int(conf_match.group(1))
            if 0 <= val <= 100:
                confidence = val
        except ValueError:
            pass

    return verdict, confidence
