"""
ANVESHA Audit Router — FastAPI endpoints for Compliance Gap Analysis.
"""

import logging
from fastapi import APIRouter, HTTPException

from app.audit.gap_analysis import run_gap_analysis, get_audit_reports

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/audit/run")
async def execute_audit():
    """
    Trigger a new compliance gap analysis.
    
    The engine will:
    1. Scan Neo4j or default frameworks for requirements/controls.
    2. Extract matching evidence from policies and systems in the graph.
    3. Run a zero-temperature LLM comparison (MET, PARTIAL, GAP).
    4. Compile audit scores, reasoning, and remediation steps.
    """
    logger.info("Compliance Audit trigger request received")
    try:
        report = await run_gap_analysis()
        return report
    except Exception as e:
        logger.error(f"Failed to execute compliance audit: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute audit: {str(e)}"
        )


@router.get("/audit/reports")
async def list_audit_reports():
    """List all stored compliance audit reports."""
    reports = get_audit_reports()
    
    if not reports:
        return {"total_reports": 0, "reports": []}
    
    report_list = []
    for report_id, report_data in reports.items():
        report_list.append({
            "report_id": report_id,
            "compliance_score": report_data.get("compliance_score", 0),
            "total_controls": report_data.get("summary", {}).get("total_controls", 0),
            "met_controls": report_data.get("summary", {}).get("met_controls", 0),
            "partial_controls": report_data.get("summary", {}).get("partial_controls", 0),
            "gap_controls": report_data.get("summary", {}).get("gap_controls", 0),
            "generated_at": report_data.get("generated_at", ""),
        })
    
    # Sort newest first
    report_list.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
    
    return {
        "total_reports": len(report_list),
        "reports": report_list
    }


@router.get("/audit/report/{report_id}")
async def get_audit_report(report_id: str):
    """Retrieve details of a specific compliance audit report."""
    reports = get_audit_reports()
    if report_id not in reports:
        raise HTTPException(
            status_code=404,
            detail=f"Audit report {report_id} not found."
        )
    return reports[report_id]


@router.get("/audit/report/{report_id}/export")
async def export_audit_report(report_id: str):
    """Export a compliance audit report as a structured Markdown document."""
    from fastapi.responses import Response
    
    reports = get_audit_reports()
    if report_id not in reports:
        raise HTTPException(
            status_code=404,
            detail=f"Audit report {report_id} not found."
        )
    
    report = reports[report_id]
    
    md = []
    md.append("# ANVESHA Compliance Audit Report")
    md.append("")
    md.append(f"**Report ID:** {report_id}")
    md.append(f"**Compliance Score:** {report.get('compliance_score', 0)}%")
    md.append("")
    md.append("---")
    md.append("")
    
    for ctrl in report.get("controls", []):
        name = ctrl.get('name', 'Unknown')
        status = ctrl.get('status', 'UNKNOWN')
        desc = ctrl.get('description', '')
        reasoning = ctrl.get('reasoning', '')
        
        md.append(f"## Control: {name}")
        md.append(f"- **Status:** {status}")
        md.append(f"- **Description:** {desc}")
        md.append("")
        
        md.append("### Evidence Found")
        if ctrl.get("evidence_found"):
            for ev in ctrl["evidence_found"]:
                md.append(f"- {ev}")
        else:
            md.append("- No direct mapped evidence found in the systems catalog.")
        md.append("")
        
        md.append("### Audit Evaluation & Rationale")
        md.append(reasoning)
        md.append("")
        
        md.append("### Remediation Roadmap Checklist")
        if ctrl.get("remediation"):
            for rem in ctrl["remediation"]:
                md.append(f"- [ ] {rem}")
        else:
            md.append("- [x] Control satisfied. No remediation action required.")
        
        md.append("")
        md.append("---")
        md.append("")
        
    md_content = "\n".join(md)
    
    headers = {
        "Content-Disposition": f"attachment; filename=anvesha_compliance_report_{report_id[:8]}.md"
    }
    return Response(content=md_content, media_type="text/markdown", headers=headers)


@router.get("/audit/report/{report_id}/annotated")
async def export_annotated_documents(report_id: str):
    """
    Export annotated source documents with colored compliance highlights.

    Returns a ZIP archive containing:
    - Annotated PDFs with colored highlights (RED=GAP, YELLOW=PARTIAL, GREEN=MET)
    - Annotated HTML report for non-PDF sources (audio, schematics, tables)
    - README with legend and instructions

    Each highlight in the PDF includes a clickable popup note with the
    control name and audit reasoning.
    """
    from fastapi.responses import Response
    from app.audit.annotated_export import generate_annotated_export

    reports = get_audit_reports()
    if report_id not in reports:
        raise HTTPException(
            status_code=404,
            detail=f"Audit report {report_id} not found."
        )

    try:
        zip_bytes, zip_filename = await generate_annotated_export(report_id)
    except Exception as e:
        logger.error(f"Annotated export failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate annotated export: {str(e)}"
        )

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={zip_filename}"
        },
    )


from pydantic import BaseModel, Field

class RemediateRequest(BaseModel):
    report_id: str = Field(..., description="Active audit report ID")
    requirement_id: str = Field(..., description="ID of the requirement/control to remediate")
    name: str = Field(..., description="Name of the control")
    description: str = Field(..., description="Description of the control")
    reasoning: str = Field(..., description="Audit reasoning/gap analysis details")

class ApplyRemediationRequest(BaseModel):
    report_id: str = Field(..., description="Active audit report ID")
    requirement_id: str = Field(..., description="ID of the requirement/control to remediate")
    code: str = Field(..., description="The remediation patch code applied")


@router.post("/audit/remediate")
async def trigger_remediation(request: RemediateRequest):
    """
    Trigger Lyzr SecOps Agent to formulate remediation steps and script.
    """
    from app.providers.lyzr_client import get_lyzr_client
    client = get_lyzr_client()

    try:
        remediation_patch = await client.run_remediation_task(
            requirement_id=request.requirement_id,
            name=request.name,
            description=request.description,
            gap_reasoning=request.reasoning
        )
        return remediation_patch
    except Exception as e:
        logger.error(f"Remediation agent failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit/remediate/apply")
async def apply_remediation(request: ApplyRemediationRequest):
    """
    Apply a remediation patch: updates Neo4j and dynamic report metrics.
    """
    from app.graph.writer import remediate_control_in_graph
    from app.audit.gap_analysis import get_audit_reports, save_audit_reports
    
    # 1. Update the graph DB
    graph_res = await remediate_control_in_graph(request.requirement_id, request.code)
    if graph_res.get("status") == "error":
        raise HTTPException(status_code=500, detail=graph_res.get("message", "Graph write failed"))

    # 2. Update active report in memory (so score updates immediately on dashboard)
    reports = get_audit_reports()
    if request.report_id in reports:
        report = reports[request.report_id]
        
        # Find the specific control inside the report and change its status
        for ctrl in report.get("controls", []):
            if ctrl["requirement_id"] == request.requirement_id or ctrl["name"] == request.requirement_id:
                old_status = ctrl["status"]
                if old_status != "MET":
                    ctrl["status"] = "MET"
                    ctrl["evidence_found"].append("Lyzr Auto-Remediation Execution Record (Neo4j link active)")
                    ctrl["reasoning"] = (
                        f"REMEDIATED: {ctrl['reasoning']}\n\n"
                        f"[Lyzr Agent Patch applied at {datetime.now(timezone.utc).isoformat()}]"
                    )
                    # Clear/update remediation steps
                    ctrl["remediation"] = []

                    # Adjust global report counts
                    report["summary"]["met_controls"] += 1
                    if old_status == "PARTIAL":
                        report["summary"]["partial_controls"] -= 1
                    elif old_status == "GAP":
                        report["summary"]["gap_controls"] -= 1

        # Recalculate compliance score
        total = report["summary"]["total_controls"]
        met = report["summary"]["met_controls"]
        report["compliance_score"] = int((met / total * 100)) if total > 0 else 0
        
        save_audit_reports()

    return {
        "status": "success",
        "requirement_id": request.requirement_id,
        "evidence_id": graph_res.get("evidence_id"),
        "mode": graph_res.get("mode"),
        "compliance_score": reports[request.report_id].get("compliance_score", 100) if request.report_id in reports else 100
    }


class ConsultRequest(BaseModel):
    requirement_id: str = Field(..., description="ID of the requirement")
    control_name: str = Field(..., description="Name of the control")
    status: str = Field(..., description="Current compliance status (MET/PARTIAL/GAP)")
    description: str = Field(..., description="Control description")
    evidence: list[str] = Field(default=[], description="List of evidence found")
    reasoning: str = Field(..., description="Audit reasoning")
    message: str = Field(..., description="The user's chat message")
    history: list[dict] = Field(default=[], description="Chat history context")


@router.post("/audit/consult")
async def trigger_consultation(request: ConsultRequest):
    """
    Spawns an interactive debate/discussion session with the ANVESHA Compliance Consultant.
    """
    from app.providers.llm_router import get_llm_router, Provider

    llm_router = get_llm_router()

    system_prompt = (
        "You are the ANVESHA Compliance Consultant Agent.\n"
        "You are discussing the compliance posture of a specific organizational control with the system engineer.\n\n"
        f"CONTROL CONTEXT:\n"
        f"- ID: {request.requirement_id}\n"
        f"- Name: {request.control_name}\n"
        f"- Status: {request.status}\n"
        f"- Description: {request.description}\n"
        f"- Evidence Found: {', '.join(request.evidence) if request.evidence else 'No evidence found.'}\n"
        f"- Audit Rationale: {request.reasoning}\n\n"
        "INSTRUCTIONS:\n"
        "1. Answer the engineer's questions objectively based on the control description and evidence.\n"
        "2. If they challenge/argue the status, explain clearly why the rating was assigned (GAP/PARTIAL/MET).\n"
        "3. Proactively analyze any alternative implementations or workarounds they suggest. Detail whether it meets the spirit of the control.\n"
        "4. Tell them what specific proof or configuration changes would move the control status to MET.\n"
        "5. Keep responses concise, professional, structured (use bullet points where appropriate), and polite."
    )

    # Format history for LLM API format (user/assistant role mapping)
    formatted_prompt = ""
    for turn in request.history[-6:]:  # Keep last 3 rounds for context
        role = "User" if turn.get("role") == "user" else "Assistant"
        formatted_prompt += f"{role}: {turn.get('content')}\n"
    
    formatted_prompt += f"User: {request.message}\nAssistant:"

    try:
        response = await llm_router.generate(
            prompt=formatted_prompt,
            system_prompt=system_prompt,
            provider=Provider.GEMINI, # Gemini is great for conversational reasoning
            temperature=0.3,
            max_tokens=1024
        )
        return {"response": response}
    except Exception as e:
        logger.error(f"Consultation debate failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



class VoiceChatRequest(BaseModel):
    question: str
    report_context: str

@router.post(""/audit/voice_chat"")
async def handle_voice_chat(request: VoiceChatRequest):
    from app.providers.llm_router import get_llm_router, Provider
    llm = get_llm_router()
    
    prompt = f"""You are the ANVESHA compliance intelligence agent.
You are having a voice conversation with a user about a compliance report.
Keep your answers CONCISE, clear, and easy to speak out loud (avoid complex markdown, tables, or code).
Answer the user's question based ONLY on the provided report context.

Report Context:
{request.report_context}

User Question: {request.question}
"""
    try:
        response = await llm.generate(prompt, provider=Provider.GROQ, temperature=0.3)
        return {"answer": response}
    except Exception as e:
        logger.error(f"Voice chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))