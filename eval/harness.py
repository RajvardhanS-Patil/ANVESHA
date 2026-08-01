"""
ANVESHA Evaluation Harness — Automated scoring pipeline.

Metrics:
- Entity Extraction: F1 (precision, recall) against gold set
- Answer Quality: keyword recall, citation presence, confidence calibration
- Abstention: correctly refuses when evidence is insufficient
- Hallucination: detects fabricated claims
- End-to-end: latency, token efficiency
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
GOLD_SET_PATH = EVAL_DIR / "qa_gold_set.json"


class EvaluationHarness:
    """Run automated evaluation against a running ANVESHA instance."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=120)
        self.results: list[dict] = []

    async def close(self):
        await self.client.aclose()

    def load_gold_set(self, path: Optional[str] = None) -> dict:
        """Load the gold standard Q&A set."""
        p = Path(path) if path else GOLD_SET_PATH
        with open(p, "r") as f:
            return json.load(f)

    async def run_full_evaluation(self, gold_set_path: Optional[str] = None) -> dict:
        """
        Run complete evaluation pipeline.

        Returns:
            Dict with per-question results and aggregate metrics
        """
        gold_set = self.load_gold_set(gold_set_path)
        questions = gold_set["questions"]

        start_time = time.perf_counter()
        self.results = []

        for i, q in enumerate(questions):
            logger.info(f"Evaluating [{i+1}/{len(questions)}]: {q['id']} — {q['question'][:60]}")
            result = await self._evaluate_question(q)
            self.results.append(result)

        elapsed = time.perf_counter() - start_time

        # Aggregate metrics
        aggregate = self._compute_aggregate_metrics()
        aggregate["total_time_seconds"] = round(elapsed, 2)
        aggregate["questions_evaluated"] = len(self.results)
        aggregate["timestamp"] = datetime.now(timezone.utc).isoformat()

        return {
            "aggregate": aggregate,
            "per_question": self.results,
        }

    async def _evaluate_question(self, q: dict) -> dict:
        """Evaluate a single question against gold standard."""
        qid = q["id"]
        question = q["question"]
        should_abstain = q.get("should_abstain", False)
        gold_keywords = q.get("gold_answer_keywords", [])
        expected_entities = q.get("expected_entities", [])

        start_time = time.perf_counter()

        try:
            # Query the system
            res = await self.client.post(
                f"{self.base_url}/api/query/verified",
                json={"question": question},
            )
            data = res.json()

            latency = time.perf_counter() - start_time
            answer = data.get("verified_answer", data.get("answer", ""))
            confidence = data.get("confidence", 0)
            citations = data.get("citations", [])
            verification = data.get("verification", {})
            abstained = verification.get("abstained", False)

            # === Metric 1: Abstention Correctness ===
            abstention_correct = abstained == should_abstain
            abstention_score = 1.0 if abstention_correct else 0.0

            # === Metric 2: Keyword Recall (only for non-abstain questions) ===
            keyword_recall = 0.0
            keyword_hits = []
            if gold_keywords and not should_abstain:
                answer_lower = answer.lower()
                hits = [kw for kw in gold_keywords if kw.lower() in answer_lower]
                keyword_hits = hits
                keyword_recall = len(hits) / len(gold_keywords) if gold_keywords else 0

            # === Metric 3: Citation Presence ===
            has_citations = len(citations) > 0
            citation_score = 1.0 if has_citations or should_abstain else 0.0

            # === Metric 4: Confidence Calibration ===
            # High confidence with correct answer = good
            # Low confidence with abstention = good
            # High confidence with wrong/no answer = bad
            if should_abstain:
                # Should have low confidence and abstain
                calibration_score = 1.0 if confidence <= 20 and abstained else 0.0
            else:
                # Should have reasonable confidence with answer
                if keyword_recall > 0.5 and confidence >= 50:
                    calibration_score = 1.0
                elif keyword_recall > 0.3 and confidence >= 30:
                    calibration_score = 0.5
                else:
                    calibration_score = keyword_recall * 0.5

            # === Metric 5: Entity Coverage ===
            entity_coverage = 0.0
            if expected_entities and not should_abstain:
                answer_lower = answer.lower()
                found = [e for e in expected_entities if e.lower() in answer_lower]
                entity_coverage = len(found) / len(expected_entities) if expected_entities else 0

            # === Composite Score ===
            if should_abstain:
                composite = abstention_score  # Only thing that matters for trap Qs
            else:
                composite = (
                    keyword_recall * 0.3 +
                    citation_score * 0.2 +
                    calibration_score * 0.2 +
                    entity_coverage * 0.2 +
                    abstention_score * 0.1
                )

            return {
                "id": qid,
                "question": question,
                "difficulty": q.get("difficulty", "basic"),
                "should_abstain": should_abstain,
                "actual_abstained": abstained,
                "answer_preview": answer[:200] + "..." if len(answer) > 200 else answer,
                "confidence": confidence,
                "latency_seconds": round(latency, 2),
                "metrics": {
                    "abstention_correct": abstention_correct,
                    "abstention_score": abstention_score,
                    "keyword_recall": round(keyword_recall, 3),
                    "keyword_hits": keyword_hits,
                    "citation_present": has_citations,
                    "citation_score": citation_score,
                    "calibration_score": round(calibration_score, 3),
                    "entity_coverage": round(entity_coverage, 3),
                    "composite_score": round(composite, 3),
                },
                "citations_count": len(citations),
                "verification_claims": verification.get("total_claims", 0),
                "supported_claims": len(verification.get("supported_claims", [])),
                "rejected_claims": len(verification.get("rejected_claims", [])),
                "status": "success",
            }

        except Exception as e:
            logger.error(f"Evaluation failed for {qid}: {e}")
            return {
                "id": qid,
                "question": question,
                "difficulty": q.get("difficulty", "basic"),
                "should_abstain": should_abstain,
                "status": "error",
                "error": str(e),
                "metrics": {
                    "composite_score": 0.0,
                },
                "latency_seconds": round(time.perf_counter() - start_time, 2),
            }

    def _compute_aggregate_metrics(self) -> dict:
        """Compute aggregate metrics across all evaluated questions."""
        if not self.results:
            return {}

        successful = [r for r in self.results if r.get("status") == "success"]
        if not successful:
            return {"error": "No successful evaluations"}

        # Aggregate scores
        composite_scores = [r["metrics"]["composite_score"] for r in successful]
        abstention_scores = [r["metrics"]["abstention_score"] for r in successful]
        keyword_recalls = [r["metrics"].get("keyword_recall", 0) for r in successful if not r.get("should_abstain")]
        citation_scores = [r["metrics"]["citation_score"] for r in successful]
        calibration_scores = [r["metrics"]["calibration_score"] for r in successful]
        latencies = [r["latency_seconds"] for r in successful]

        # By difficulty
        by_difficulty = {}
        for r in successful:
            diff = r.get("difficulty", "basic")
            if diff not in by_difficulty:
                by_difficulty[diff] = []
            by_difficulty[diff].append(r["metrics"]["composite_score"])

        return {
            "overall_composite": round(sum(composite_scores) / len(composite_scores), 3),
            "abstention_accuracy": round(sum(abstention_scores) / len(abstention_scores), 3),
            "avg_keyword_recall": round(sum(keyword_recalls) / len(keyword_recalls), 3) if keyword_recalls else 0,
            "avg_citation_score": round(sum(citation_scores) / len(citation_scores), 3),
            "avg_calibration": round(sum(calibration_scores) / len(calibration_scores), 3),
            "avg_latency_seconds": round(sum(latencies) / len(latencies), 2),
            "p95_latency_seconds": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0,
            "total_questions": len(self.results),
            "successful_questions": len(successful),
            "failed_questions": len(self.results) - len(successful),
            "by_difficulty": {
                diff: round(sum(scores) / len(scores), 3)
                for diff, scores in by_difficulty.items()
            },
        }

    async def run_entity_f1(self, doc_id: str, gold_entities: list[dict]) -> dict:
        """
        Evaluate entity extraction F1 for a specific document.

        Args:
            doc_id: Document ID to evaluate
            gold_entities: List of gold standard entities [{name, type}]

        Returns:
            Dict with precision, recall, F1
        """
        try:
            res = await self.client.get(f"{self.base_url}/api/graph")
            graph = res.json()

            # Get extracted entities
            extracted = set()
            for node in graph.get("nodes", []):
                name = node.get("name", "").lower()
                etype = node.get("type", "").lower()
                extracted.add((name, etype))

            # Gold entities
            gold = set()
            for e in gold_entities:
                gold.add((e["name"].lower(), e["type"].lower()))

            # F1 calculation
            tp = len(extracted & gold)
            fp = len(extracted - gold)
            fn = len(gold - extracted)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

            return {
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "extracted_count": len(extracted),
                "gold_count": len(gold),
            }

        except Exception as e:
            logger.error(f"Entity F1 evaluation failed: {e}")
            return {"error": str(e)}


# === CLI Runner ===
async def run_eval_cli():
    """Run evaluation from command line."""
    import sys

    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    gold_path = sys.argv[2] if len(sys.argv) > 2 else None

    harness = EvaluationHarness(base_url)
    try:
        results = await harness.run_full_evaluation(gold_path)
        print(json.dumps(results, indent=2))

        # Save results
        output_path = EVAL_DIR / f"eval_results_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {output_path}")

    finally:
        await harness.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_eval_cli())
