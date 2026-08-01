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
    return {
        "total_reports": len(reports),
        "reports": [
            {
                "report_id": rid,
                "compliance_score": r.get("compliance_score", 0),
                "total_controls": r.get("summary", {}).get("total_controls", 0),
                "met_controls": r.get("summary", {}).get("met_controls", 0),
                "partial_controls": r.get("summary", {}).get("partial_controls", 0),
                "gap_controls": r.get("summary", {}).get("gap_controls", 0),
                "generated_at": r.get("generated_at", ""),
            }
            for rid, r in reports.items()
        ]
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
    from fastapi.responses import PlainTextResponse
    reports = get_audit_reports()
    if report_id not in reports:
        raise HTTPException(
            status_code=404,
            detail=f"Audit report {report_id} not found."
        )
    
    report = reports[report_id]
    
    md = []
    md.append(f"# ANVESHA Compliance Audit Report")
    md.append(f"**Report ID**: {report['report_id']}")
    md.append(f"**Generated At**: {report['generated_at']}")
    md.append(f"**Compliance Score**: {report['compliance_score']}%")
    md.append("")
    md.append("## Executive Summary")
    md.append("| Metric | Value |")
    md.append("| --- | --- |")
    md.append(f"| Total Controls Audited | {report['summary']['total_controls']} |")
    md.append(f"| Controls Met (Compliant) | {report['summary']['met_controls']} |")
    md.append(f"| Partial Gaps | {report['summary']['partial_controls']} |")
    md.append(f"| Critical Gaps (Non-Compliant) | {report['summary']['gap_controls']} |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Detailed Control Assessment")
    md.append("")
    
    for ctrl in report["controls"]:
        status_symbol = "✅ MET" if ctrl["status"] == "MET" else "⚠️ PARTIAL" if ctrl["status"] == "PARTIAL" else "❌ GAP"
        md.append(f"### {ctrl['name']}")
        md.append(f"**Framework**: {ctrl['framework']} | **Category**: {ctrl['category']}")
        md.append(f"**Status**: {status_symbol}")
        md.append("")
        md.append(f"**Description**:")
        md.append(f"> {ctrl['description']}")
        md.append("")
        
        md.append("**Evidence Found**:")
        if ctrl.get("evidence_found"):
            for ev in ctrl["evidence_found"]:
                md.append(f"- {ev}")
        else:
            md.append("- No direct mapped evidence found in the systems catalog.")
        md.append("")
        
        md.append(f"**Audit Evaluation & Rationale**:")
        md.append(ctrl["reasoning"])
        md.append("")
        
        md.append("**Remediation Roadmap Checklist**:")
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
    return PlainTextResponse(content=md_content, headers=headers)
