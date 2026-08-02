"""
ANVESHA Compliance Gap Analysis Engine — Auto-Auditor.

Compares regulatory/compliance requirements against company policies, systems,
and evidence in the graph. Generates structured MET, PARTIAL, or GAP compliance ratings,
detailed reasoning, and remediation tasks.
"""

import logging
import uuid
import time
import json
import os
from typing import Optional, Any
from datetime import datetime, timezone

from app.config import get_settings
from app.graph.neo4j_client import get_neo4j_client
from app.providers.llm_router import get_llm_router, Provider

logger = logging.getLogger(__name__)

# --- Default Compliance Framework baseline for fallback & structured auditing ---
DEFAULT_COMPLIANCE_BASELINE = [
    # GDPR Baseline
    {
        "id": "GDPR-ART-5-1-F",
        "framework": "GDPR",
        "category": "Data Minimization & Security",
        "name": "Article 5(1)(f) - Integrity and Confidentiality",
        "description": "Personal data must be processed in a manner that ensures appropriate security, including protection against unauthorized or unlawful processing and against accidental loss, destruction or damage, using appropriate technical or organizational measures."
    },
    {
        "id": "GDPR-ART-32",
        "framework": "GDPR",
        "category": "Technical Controls",
        "name": "Article 32 - Security of Processing",
        "description": "Implementation of appropriate technical and organizational measures to ensure a level of security appropriate to the risk, including encryption of personal data, maintaining ongoing confidentiality, integrity, availability, and resilience of processing systems."
    },
    {
        "id": "GDPR-ART-33",
        "framework": "GDPR",
        "category": "Incident Response",
        "name": "Article 33 - Notification of Personal Data Breach",
        "description": "In the case of a personal data breach, the controller shall without undue delay and, where feasible, not later than 72 hours after having become aware of it, notify the personal data breach to the supervisory authority."
    },
    # ISO 27001 Baseline
    {
        "id": "ISO-A-8-24",
        "framework": "ISO 27001",
        "category": "Access Control",
        "name": "Control A.8.24 - Use of Cryptography",
        "description": "Rules for the effective use of cryptography, including cryptographic key management, should be defined and implemented to protect the confidentiality, authenticity, and integrity of information."
    },
    {
        "id": "ISO-A-8-20",
        "framework": "ISO 27001",
        "category": "Network Security",
        "name": "Control A.8.20 - Network Security",
        "description": "Networks and network devices should be secured, managed, and controlled to protect information in systems and applications."
    },
    {
        "id": "ISO-A-5-36",
        "framework": "ISO 27001",
        "category": "Incident Management",
        "name": "Control A.5.36 - Compliance with Policies",
        "description": "The compliance of systems and processes with organization's information security policies and standards should be regularly reviewed."
    }
]

# In-memory storage for past audit reports
_audit_reports: dict[str, dict] = {}
_audit_reports_file = os.path.join(os.path.dirname(__file__), "audit_reports.json")

def _load_reports():
    global _audit_reports
    if os.path.exists(_audit_reports_file):
        try:
            with open(_audit_reports_file, 'r', encoding='utf-8') as f:
                _audit_reports = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load audit reports: {e}")

def save_audit_reports():
    try:
        with open(_audit_reports_file, 'w', encoding='utf-8') as f:
            json.dump(_audit_reports, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save audit reports: {e}")

_load_reports()

def get_audit_reports() -> dict:
    """Get the in-memory audit reports store."""
    return _audit_reports


GAP_ANALYSIS_SYSTEM_PROMPT = """You are ANVESHA, a professional enterprise compliance auditor.
Your task is to perform a Compliance Gap Analysis.
You will compare a specific Regulatory/Standard Requirement against the Company Evidence, Policies, and Systems provided.

Evaluate the status using exactly one of these ratings:
- MET: The evidence demonstrates complete, solid coverage of the requirement.
- PARTIAL: The evidence addresses the requirement partially, but lacks implementation details, leaves room for risks, or is missing key sub-controls.
- GAP: The evidence does not address this requirement at all, or there is zero mapped policy/system supporting it.

You MUST respond ONLY with a valid JSON object matching the following schema:
{
    "requirement_id": "string",
    "status": "MET" | "PARTIAL" | "GAP",
    "evidence_found": ["string", "string"],
    "reasoning": "Detailed, professional explanation of why this rating was given, referencing the evidence.",
    "remediation": ["Action item 1", "Action item 2"]
}
"""

GAP_ANALYSIS_USER_PROMPT = """Evaluate this requirement against the provided company compliance context.

REQUIREMENT:
- Name: {req_name}
- Category: {req_category}
- Description: {req_desc}

COMPANY COMPLIANCE CONTEXT (Evidence, Policies, and Systems):
{evidence}

Analyze carefully. If the evidence mentions details but not security guarantees, rate it PARTIAL. If nothing matches, rate it GAP.
"""

async def run_gap_analysis(doc_id: Optional[str] = None) -> dict:
    """
    Run an end-to-end compliance gap analysis.
    If Neo4j is connected, queries the graph database for requirements and evidence.
    Otherwise, falls back to scanning the local document store.
    """
    start_time = time.perf_counter()
    report_id = str(uuid.uuid4())
    logger.info(f"Starting compliance gap analysis audit: {report_id}")

    neo4j_client = get_neo4j_client()
    llm_router = get_llm_router()

    requirements_to_audit = []
    
    # 1. Gather requirements to audit
    if neo4j_client.is_connected():
        try:
            # Fetch all requirements or controls in the graph
            query = """
            MATCH (r:Entity) 
            WHERE r.entity_type IN ['Requirement', 'Control', 'Regulation']
            RETURN r.id AS id, r.name AS name, r.entity_type AS category, r.description AS description
            LIMIT 20
            """
            results = await neo4j_client.execute_read(query)
            if results:
                requirements_to_audit = [
                    {
                        "id": r["id"],
                        "framework": "Graph",
                        "category": r["category"],
                        "name": r["name"],
                        "description": r["description"] or ""
                    }
                    for r in results
                ]
                logger.info(f"Found {len(requirements_to_audit)} requirements in the Knowledge Graph for auditing")
        except Exception as e:
            logger.warning(f"Failed to query requirements from graph: {e}")

    # Fallback to default framework baseline if graph is empty or disconnected
    if not requirements_to_audit:
        logger.info("Using default compliance framework baseline for audit")
        requirements_to_audit = DEFAULT_COMPLIANCE_BASELINE

    audited_results = []
    met_count = 0
    partial_count = 0
    gap_count = 0

    # 2. Audit each requirement
    for req in requirements_to_audit:
        evidence_context = ""
        
        # 2a. Gather context for this requirement
        if neo4j_client.is_connected():
            try:
                # Find connected nodes in graph
                query_base = """
                MATCH (req:Entity) WHERE req.id = $req_id
                OPTIONAL MATCH path = (req)-[r:IMPLEMENTED_BY|EVIDENCED_BY|APPLIES_TO|RESPONSIBLE_FOR|RELATED_TO*1..2]-(evidence:Entity)
                WHERE evidence.entity_type IN ['Policy', 'System', 'Evidence', 'Asset']
                """
                if doc_id:
                    query_base += " AND evidence.source_doc_id = $doc_id\n"
                    
                query_base += """
                RETURN DISTINCT evidence {
                    .id, .name, .entity_type, .description, .exact_span, .source_doc_id, .source_location
                } AS ev
                LIMIT 10
                """
                rels = await neo4j_client.execute_read(query_base, {"req_id": req["id"], "doc_id": doc_id})
                
                # Check for vector fallback if no direct path
                if not rels or not rels[0] or not rels[0].get("ev"):
                    # Use vector search to retrieve similar policy/evidence chunks
                    req_embedding = await llm_router.embed(f"{req['name']} {req['description']}")
                    # If doc_id is provided, vector search filtering would be needed here, 
                    # but for now we rely on the graph query filtering or fallback search filtering.
                    vector_matches = await neo4j_client.vector_search(req_embedding, top_k=5)
                    
                    if doc_id:
                        vector_matches = [m for m in vector_matches if m.get("entity", {}).get("source_doc_id") == doc_id]
                        
                    evidence_context = _format_vector_matches_for_audit(vector_matches)
                else:
                    evidence_context = _format_graph_evidence_for_audit(rels)
            except Exception as e:
                logger.warning(f"Graph context retrieval failed for {req['name']}: {e}")

        # 2b. In-memory chunk store fallback context
        if not evidence_context.strip():
            evidence_context = await _fallback_text_retrieval_for_audit(req["name"] + " " + req["description"], doc_id)

        # 2c. Run LLM audit comparison
        try:
            user_prompt = GAP_ANALYSIS_USER_PROMPT.format(
                req_name=req["name"],
                req_category=req.get("category") or req.get("framework") or "Compliance",
                req_desc=req["description"],
                evidence=evidence_context
            )
            
            # Using generate_json to enforce the structured schema
            # Use GROQ for speed (30 RPM) with JSON mode; fallback to GEMINI handled by router
            import asyncio
            analysis = await asyncio.wait_for(
                llm_router.generate_json(
                    prompt=user_prompt,
                    system_prompt=GAP_ANALYSIS_SYSTEM_PROMPT,
                    provider=Provider.GROQ,  # Groq is faster (30 RPM), supports JSON mode
                    temperature=0.0
                ),
                timeout=45.0  # 45s max per control
            )

            # Validate LLM output format
            if "status" in analysis:
                status = analysis["status"].upper()
                if status not in ["MET", "PARTIAL", "GAP"]:
                    status = "GAP"
            else:
                # Handle direct extraction fail
                status = "GAP"
                analysis = {
                    "requirement_id": req["id"],
                    "status": "GAP",
                    "evidence_found": [],
                    "reasoning": "Audit analysis failed. Defaulting to GAP for safety.",
                    "remediation": ["Manually review system designs against this requirement."]
                }

            # Increment count
            if status == "MET":
                met_count += 1
            elif status == "PARTIAL":
                partial_count += 1
            else:
                gap_count += 1

            audited_results.append({
                "requirement_id": req["id"],
                "name": req["name"],
                "framework": req.get("framework") or "System",
                "category": req.get("category") or "General Security",
                "description": req["description"],
                "status": status,
                "evidence_found": analysis.get("evidence_found", []),
                "reasoning": analysis.get("reasoning", "No detailed reasoning provided."),
                "remediation": analysis.get("remediation", [])
            })

        except asyncio.TimeoutError:
            logger.error(f"Audit analysis timed out for control {req['name']}")
            gap_count += 1
            audited_results.append({
                "requirement_id": req["id"],
                "name": req["name"],
                "framework": req.get("framework") or "System",
                "category": req.get("category") or "General Security",
                "description": req["description"],
                "status": "GAP",
                "evidence_found": [],
                "reasoning": "Audit evaluation timed out. Please retry or check API connectivity.",
                "remediation": ["Retry the compliance audit when API is available."]
            })
        except Exception as e:
            logger.error(f"Audit analysis failed for control {req['name']}: {e}")
            gap_count += 1
            audited_results.append({
                "requirement_id": req["id"],
                "name": req["name"],
                "framework": req.get("framework") or "System",
                "category": req.get("category") or "General Security",
                "description": req["description"],
                "status": "GAP",
                "evidence_found": [],
                "reasoning": f"Audit evaluation failed due to exception: {e}",
                "remediation": ["Review requirement mapping manually."]
            })

    # 3. Calculate summary metrics
    total = len(requirements_to_audit)
    compliance_score = int((met_count / total * 100)) if total > 0 else 0
    elapsed = time.perf_counter() - start_time

    report = {
        "report_id": report_id,
        "compliance_score": compliance_score,
        "summary": {
            "total_controls": total,
            "met_controls": met_count,
            "partial_controls": partial_count,
            "gap_controls": gap_count,
        },
        "controls": audited_results,
        "metadata": {
            "processing_time_seconds": round(elapsed, 2),
            "engine": "ANVESHA-AutoAuditor-v1.0"
        },
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Store report in-memory
    _audit_reports[report_id] = report
    save_audit_reports()
    logger.info(f"Audit complete: score={compliance_score}%, MET={met_count}, GAP={gap_count} in {elapsed:.2f}s")
    
    return report


def _format_graph_evidence_for_audit(rels: list) -> str:
    """Format graph evidence relationships as clean text for LLM comparison."""
    parts = []
    for r in rels:
        ev = r.get("ev")
        if not ev:
            continue
        parts.append(
            f"- Connected {ev.get('entity_type', 'Entity')}: {ev.get('name', 'Unnamed')}\n"
            f"  Description: {ev.get('description', 'N/A')}\n"
            f"  Span: \"{ev.get('exact_span', 'N/A')}\"\n"
            f"  Source: Document {ev.get('source_doc_id', 'N/A')} at {ev.get('source_location', 'N/A')}"
        )
    return "\n\n".join(parts) if parts else "No direct compliance evidence linked in graph."


def _format_vector_matches_for_audit(matches: list) -> str:
    """Format vector similarity matches as text for LLM comparison."""
    parts = []
    for idx, match in enumerate(matches):
        entity = match.get("entity", {})
        score = match.get("score", 0)
        parts.append(
            f"- Semantic Match #{idx+1} [Similarity: {score:.2f}]: {entity.get('name', 'Unnamed')} ({entity.get('entity_type', 'Entity')})\n"
            f"  Description: {entity.get('description', 'N/A')}\n"
            f"  Span: \"{entity.get('exact_span', 'N/A')}\"\n"
            f"  Source: Document {entity.get('source_doc_id', 'N/A')}"
        )
    return "\n\n".join(parts) if parts else "No semantic evidence found via vector search."


async def _fallback_text_retrieval_for_audit(keywords: str, doc_id: Optional[str] = None) -> str:
    """Fallback: retrieve text chunks from chunk store when offline/empty graph."""
    from app.ingestion.router import get_chunk_store
    chunk_store = get_chunk_store()
    if not chunk_store:
        return "No local company documents or policies uploaded to evaluate."

    # Search for matching words
    search_words = set(keywords.lower().split())
    scored_chunks = []

    for chunk_id, chunk in chunk_store.items():
        if doc_id and chunk.get("doc_id") != doc_id:
            continue
            
        text = chunk.get("raw_text", "").lower()
        if not text:
            continue
        matches = sum(1 for w in search_words if w in text and len(w) > 3)
        if matches > 0:
            scored_chunks.append((matches, chunk))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    top_chunks = scored_chunks[:3]

    if not top_chunks:
        return "No matching local documents or policy descriptions found in the upload store."

    parts = []
    for idx, (score, chunk) in enumerate(top_chunks):
        parts.append(
            f"Document snippet #{idx+1} (Source: {chunk.get('source_filename', 'unknown')}):\n"
            f"Content: {chunk.get('raw_text', '')[:1000]}"
        )
    return "\n\n---\n\n".join(parts)
