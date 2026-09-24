"""
Phase 9 — Trends Service.

Feeds the Trends page (9B in the master prompt): score trend, leakage
trend, mistake-type evolution, chapter mastery evolution, and behavioral
(panic / second-guess) trend across all of a student's analyzed tests.

This file is entirely additive and read-only — it only ever SELECTs from
tables that already exist (test_sessions, question_results, chapters).
It writes nothing, so it cannot regress the pipeline or any earlier phase.
Only sessions with status='submitted' AND analysis_ready=1 are included,
so a test that's mid-pipeline never shows up with half-formed numbers.
"""

import json
import statistics
from collections import defaultdict
from backend.models.database import get_db

# Fixed, stable ordering so the stacked mistake-evolution chart never
# reshuffles its series/colors between renders.
MISTAKE_TYPES = [
    "conceptual", "formula_recall", "calculation", "misread",
    "time_pressure", "guessing", "careless", "avoidance", "unclassified",
]


def _linreg(xs, ys):
    """Plain least-squares slope/intercept — no numpy dependency needed
    for a straight trendline over at most a few dozen points."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0, mean_y
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


class TrendsService:
    def __init__(self, student_id: int):
        self.student_id = student_id

    def _sessions(self):
        conn = get_db()
        rows = conn.execute("""
            SELECT s.id, s.submitted_at, s.raw_score, s.max_score,
                   s.leakage_report_json, s.behavioral_flags_json,
                   t.name as template_name
            FROM test_sessions s
            JOIN test_templates t ON t.id = s.template_id
            WHERE s.student_id=? AND s.status='submitted' AND s.analysis_ready=1
            ORDER BY s.submitted_at ASC
        """, (self.student_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_trends(self) -> dict:
        sessions = self._sessions()

        # Need at least 2 analyzed tests for any of this to mean "evolution".
        if len(sessions) < 2:
            return {
                "sufficient_data": False,
                "sessions_analyzed": len(sessions),
                "score_trend": [], "score_trendline": [],
                "leakage_trend": [], "mistake_evolution": [],
                "chapter_mastery_evolution": [], "behavioral_improvements": [],
                "panic_frequency": {"tests_with_panic": 0, "total_tests": len(sessions), "rate": 0},
            }

        session_ids = [s["id"] for s in sessions]
        placeholders = ",".join("?" * len(session_ids))

        conn = get_db()
        qr_rows = conn.execute(f"""
            SELECT qr.session_id, qr.chapter_id, ch.name as chapter_name,
                   qr.is_correct, qr.is_skipped, qr.classified_mistake_type,
                   qr.answer_change_count
            FROM question_results qr
            JOIN chapters ch ON ch.id = qr.chapter_id
            WHERE qr.session_id IN ({placeholders})
        """, session_ids).fetchall()
        conn.close()

        qr_by_session = defaultdict(list)
        for r in qr_rows:
            qr_by_session[r["session_id"]].append(dict(r))

        # ── score trend + trendline ─────────────────────────────────────
        score_trend = []
        for s in sessions:
            pct = round((s["raw_score"] / s["max_score"]) * 100, 1) if s["max_score"] else 0.0
            score_trend.append({
                "session_id": s["id"], "date": (s["submitted_at"] or "")[:10],
                "template_name": s["template_name"],
                "raw_score": s["raw_score"], "max_score": s["max_score"],
                "percentage": pct,
            })
        xs = list(range(len(score_trend)))
        ys = [p["percentage"] for p in score_trend]
        slope, intercept = _linreg(xs, ys)
        score_trendline = [round(slope * x + intercept, 1) for x in xs]

        # ── leakage trend (declining = student fixing behavior) ──────────
        leakage_trend = []
        for s in sessions:
            leakage = json.loads(s["leakage_report_json"] or "{}")
            total = (leakage.get("total_leaked_from_wrong", 0) or 0) + \
                    (leakage.get("total_opportunity_cost_from_skipped", 0) or 0)
            leakage_trend.append({
                "session_id": s["id"], "date": (s["submitted_at"] or "")[:10],
                "total_leaked": round(total, 2),
                "top_leakage_source": leakage.get("top_leakage_source"),
            })

        # ── mistake evolution — % of that test's mistakes per type ──────
        mistake_evolution = []
        for s in sessions:
            rows = qr_by_session.get(s["id"], [])
            mistakes = [r for r in rows if r["is_skipped"] or not r["is_correct"]]
            total = len(mistakes)
            counts = defaultdict(int)
            for r in mistakes:
                counts[r["classified_mistake_type"] or "unclassified"] += 1
            percentages = {
                mtype: round((counts.get(mtype, 0) / total) * 100, 1) if total else 0.0
                for mtype in MISTAKE_TYPES
            }
            mistake_evolution.append({
                "session_id": s["id"], "date": (s["submitted_at"] or "")[:10],
                "total_mistakes": total, "percentages": percentages,
            })

        # ── chapter mastery evolution ─────────────────────────────────────
        chapter_points = defaultdict(list)
        chapter_names = {}
        for s in sessions:
            rows = qr_by_session.get(s["id"], [])
            by_chapter = defaultdict(lambda: {"correct": 0, "attempted": 0})
            for r in rows:
                if r["is_skipped"]:
                    continue
                cid = r["chapter_id"]
                chapter_names[cid] = r["chapter_name"]
                by_chapter[cid]["attempted"] += 1
                if r["is_correct"]:
                    by_chapter[cid]["correct"] += 1
            for cid, agg in by_chapter.items():
                acc = round(agg["correct"] / agg["attempted"] * 100, 1) if agg["attempted"] else None
                chapter_points[cid].append({
                    "session_id": s["id"], "date": (s["submitted_at"] or "")[:10],
                    "accuracy": acc,
                })

        chapter_mastery_evolution = []
        for cid, points in chapter_points.items():
            valid = [p["accuracy"] for p in points if p["accuracy"] is not None]
            if len(valid) < 2:
                trend = "flat"
            else:
                mid = len(valid) // 2 or 1
                first_half = statistics.mean(valid[:mid])
                second_half = statistics.mean(valid[mid:])
                if second_half - first_half >= 5:
                    trend = "improving"
                elif first_half - second_half >= 5:
                    trend = "declining"
                else:
                    trend = "flat"
            chapter_mastery_evolution.append({
                "chapter_id": cid, "chapter_name": chapter_names.get(cid, "Unknown"),
                "points": points, "trend": trend,
            })
        chapter_mastery_evolution.sort(key=lambda c: c["chapter_name"])

        # ── behavioral improvements ───────────────────────────────────────
        behavioral_improvements = []
        for s in sessions:
            flags = json.loads(s["behavioral_flags_json"] or "{}")
            panic = flags.get("panic", {})
            rows = qr_by_session.get(s["id"], [])
            changes = [r["answer_change_count"] or 0 for r in rows]
            avg_changes = round(statistics.mean(changes), 2) if changes else 0.0
            behavioral_improvements.append({
                "session_id": s["id"], "date": (s["submitted_at"] or "")[:10],
                "panic_detected": bool(panic.get("detected")),
                "marks_lost_to_panic": panic.get("marks_lost_to_panic", 0) or 0,
                "avg_answer_changes_per_question": avg_changes,
            })

        panic_tests = sum(1 for b in behavioral_improvements if b["panic_detected"])

        return {
            "sufficient_data": True,
            "sessions_analyzed": len(sessions),
            "score_trend": score_trend,
            "score_trendline": score_trendline,
            "leakage_trend": leakage_trend,
            "mistake_evolution": mistake_evolution,
            "chapter_mastery_evolution": chapter_mastery_evolution,
            "behavioral_improvements": behavioral_improvements,
            "panic_frequency": {
                "tests_with_panic": panic_tests,
                "total_tests": len(sessions),
                "rate": round(panic_tests / len(sessions), 2) if sessions else 0,
            },
        }
