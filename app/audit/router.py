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
