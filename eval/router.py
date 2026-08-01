"""
ANVESHA Evaluation Router — API endpoints for running evaluations.
"""

import logging
from fastapi import APIRouter
from eval.harness import EvaluationHarness

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/eval/run")
async def run_evaluation():
    """
    Run the full evaluation harness against the current system.

    Evaluates all 15 gold standard questions and returns metrics.
    """
    logger.info("Starting evaluation run...")

    harness = EvaluationHarness(base_url="http://127.0.0.1:8000")
    try:
        results = await harness.run_full_evaluation()
        logger.info(
            f"Evaluation complete: composite={results['aggregate'].get('overall_composite', 0)}"
        )
        return results
    finally:
        await harness.close()


@router.get("/eval/gold-set")
async def get_gold_set():
    """Get the gold standard Q&A set for inspection."""
    harness = EvaluationHarness()
    gold_set = harness.load_gold_set()
    return {
        "total_questions": len(gold_set["questions"]),
        "questions": gold_set["questions"],
    }
