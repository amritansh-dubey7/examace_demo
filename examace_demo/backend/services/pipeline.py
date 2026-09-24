"""
Phase 3 — Post-submit analysis pipeline.

run_post_submit_pipeline() is the single entry point fired as a background
task from POST /api/sessions/{id}/submit (wired in main.py via
backend.services.test_session_service.submit_session_route). It runs all
15 steps from the master prompt's Phase 3 spec in order. Steps 1-10 and
13-15 are implemented here using EventAnalyzer / MistakeClassifier /
RankLeakageEngine / PopulationBenchmarkUpdater from behavioral.py.

Steps 11 (Digital Twin update) and 12 (revision queue auto-creation) are
explicitly out of scope for Phase 3 per the master prompt — they're marked
[Phase 4] and [Phase 7] respectively in the spec. This file calls two small
no-op hook functions in their place so Phase 4/7 can drop in real
implementations later without changing this file's control flow or the
step numbering, and so a partial failure in a not-yet-built future step
can never block the steps that DO belong to Phase 3.

Every step is wrapped individually: if one step raises, the error and its
timing are logged and the pipeline continues to the next step, per the
spec ("Try each step independently. If one fails, log error and continue.").
This means a session can end up with analysis_ready=1 but a partially
incomplete behavioral_flags_json if, say, fatigue computation hit a bug —
better than the whole pipeline silently never finishing.
"""

import json
import logging
import time
from backend.models.database import get_db
from backend.services.behavioral import (
    EventAnalyzer, MistakeClassifier, RankLeakageEngine, PopulationBenchmarkUpdater,
)

logger = logging.getLogger("examace.pipeline")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[pipeline] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def _step(label):
    """Decorator-ish helper used inline (not a real decorator, since each
    step needs different args) — just a tiny timing+logging context manager."""
    class _Ctx:
        def __enter__(self):
            self.t0 = time.monotonic()
            return self
        def __exit__(self, exc_type, exc, tb):
            elapsed = time.monotonic() - self.t0
            if exc:
                logger.error("FAILED  %-28s %.3fs  error=%s: %s", label, elapsed, exc_type.__name__, exc)
            else:
                logger.info("ok      %-28s %.3fs", label, elapsed)
            return True  # swallow exceptions; pipeline continues per spec
    return _Ctx()


def _update_question_result(conn, session_id, question_id, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [session_id, question_id]
    conn.execute(
        f"UPDATE question_results SET {set_clause} WHERE session_id=? AND question_id=?",
        values,
    )


def _digital_twin_update_hook(student_id: int, session_id: int):
    """
    [Phase 4] Calls DigitalTwinEngine to update students.profile_json.
    Idempotent: the engine checks sessions_analyzed before processing.
    """
    from backend.services.digital_twin import DigitalTwinEngine
    DigitalTwinEngine(student_id).update(session_id)


def _revision_queue_hook(student_id: int, session_id: int):
    """
    Phase 7 — Auto-populate the spaced-repetition revision queue
    from classified mistakes for the completed session.
    """
    from backend.services.revision import RevisionService
    RevisionService(student_id).auto_create_from_session(session_id)


async def run_post_submit_pipeline(session_id: int, student_id: int):
    pipeline_start = time.monotonic()
    logger.info("=== pipeline start session_id=%s student_id=%s ===", session_id, student_id)

    conn = get_db()
    session = conn.execute("""
        SELECT s.*, t.exam_target, t.duration_mins
        FROM test_sessions s JOIN test_templates t ON t.id = s.template_id
        WHERE s.id=?
    """, (session_id,)).fetchone()
    conn.close()

    if not session:
        logger.error("session %s not found, aborting pipeline", session_id)
        return

    test_duration_mins = session["duration_mins"] or 180
    exam_target = (session["exam_target"] or "JEE").upper()
    analyzer = EventAnalyzer(session_id)
    behavioral_flags = {}

    # ── Step 1: time_spent_ms + time_ratio ────────────────────────────────
    with _step("1: time_per_question"):
        times = analyzer.compute_time_per_question()
        conn = get_db()
        for qid, ms in times.items():
            q = conn.execute("SELECT population_avg_time_secs FROM questions WHERE id=?", (qid,)).fetchone()
            pop_time = (q["population_avg_time_secs"] if q and q["population_avg_time_secs"] else 120)
            time_ratio = round((ms / 1000.0) / pop_time, 3) if pop_time else None
            _update_question_result(conn, session_id, qid, time_spent_ms=ms, time_ratio=time_ratio)
        conn.commit()
        conn.close()

    # ── Step 2: answer_change_count + answer_change_log ───────────────────
    with _step("2: answer_change_log"):
        change_logs = analyzer.compute_answer_change_log()
        conn = get_db()
        for qid, log in change_logs.items():
            _update_question_result(
                conn, session_id, qid,
                answer_change_count=len(log),
                answer_change_log=json.dumps(log),
            )
        conn.commit()
        conn.close()

    # ── Step 3: confidence_rating from ConfidenceRated events ─────────────
    with _step("3: confidence_rating"):
        conn = get_db()
        rows = conn.execute(
            "SELECT question_id, event_data, occurred_at FROM test_events "
            "WHERE session_id=? AND event_type='ConfidenceRated' ORDER BY occurred_at ASC",
            (session_id,),
        ).fetchall()
        latest_rating = {}
        for r in rows:
            data = json.loads(r["event_data"] or "{}")
            if r["question_id"] is not None:
                latest_rating[r["question_id"]] = data.get("rating")
        for qid, rating in latest_rating.items():
            _update_question_result(conn, session_id, qid, confidence_rating=rating)
        conn.commit()
        conn.close()

    # ── Step 4: fatigue curve ──────────────────────────────────────────────
    fatigue_curve = []
    with _step("4: fatigue_curve"):
        fatigue_curve = analyzer.compute_fatigue_curve(test_duration_mins)
        fatigue_detection = analyzer.detect_fatigue(fatigue_curve)
        behavioral_flags["fatigue"] = {"curve": fatigue_curve, **fatigue_detection}

    # ── Step 5: panic detection ────────────────────────────────────────────
    panic_qids = set()
    with _step("5: panic_detection"):
        panic = analyzer.detect_panic(test_duration_mins)
        behavioral_flags["panic"] = panic
        panic_qids = analyzer.get_panic_window_question_ids(test_duration_mins)
        conn = get_db()
        for qid in panic_qids:
            _update_question_result(conn, session_id, qid, in_panic_window=1)
        conn.commit()
        conn.close()

    # ── Step 6: skip pattern ───────────────────────────────────────────────
    with _step("6: skip_pattern"):
        behavioral_flags["skip_pattern"] = analyzer.compute_skip_pattern()

    # ── Step 7: section allocation ─────────────────────────────────────────
    with _step("7: section_allocation"):
        behavioral_flags["section_allocation"] = analyzer.compute_section_allocation(exam_target)

    # ── Step 8: question selection quality ─────────────────────────────────
    with _step("8: question_selection"):
        behavioral_flags["question_selection"] = analyzer.compute_question_selection_quality()

    # Persist behavioral_flags_json now so steps 9-10 (which read was_revisited
    # / in_panic_window back from question_results) see consistent state, and
    # so a failure in a later step doesn't lose steps 4-8's work.
    with _step("4-8: persist behavioral_flags_json"):
        conn = get_db()
        conn.execute(
            "UPDATE test_sessions SET behavioral_flags_json=? WHERE id=?",
            (json.dumps(behavioral_flags), session_id),
        )
        conn.commit()
        conn.close()

    # ── Step 9: mistake classification ─────────────────────────────────────
    with _step("9: mistake_classification"):
        classifier = MistakeClassifier()
        conn = get_db()
        results = conn.execute(
            "SELECT * FROM question_results WHERE session_id=? AND (is_correct=0 OR is_skipped=1)",
            (session_id,),
        ).fetchall()
        for r in results:
            qr = dict(r)
            mtype, mconf = classifier.classify(
                question_result=qr,
                time_spent_ms=qr["time_spent_ms"] or 0,
                answer_changes=qr["answer_change_count"] or 0,
                answer_change_log=json.loads(qr["answer_change_log"] or "[]"),
                population_avg_time_secs=qr["population_avg_time_secs"],
                population_accuracy=qr["population_accuracy"],
                was_revisited=bool(qr["was_revisited"]),
                confidence_rating=qr["confidence_rating"],
                in_panic_window=bool(qr["in_panic_window"]),
                exam_target=exam_target,
            )
            # Per the Phase 3 prompt addition: no classification is ever
            # stored without a confidence value.
            conn.execute(
                "UPDATE question_results SET classified_mistake_type=?, mistake_confidence=? WHERE id=?",
                (mtype, mconf if mconf is not None else 0.0, qr["id"]),
            )
        conn.commit()
        conn.close()

    # ── Step 9b: ambiguous-correct detection [Phase 7] ──────────────────────
    # Runs only on is_correct=1 rows — separate and additive to Step 9, which
    # only ever classifies wrong/skipped answers. Flags "right but shaky"
    # answers (guessed, took too long, or flip-flopped to the right option)
    # so the student sees a yellow warning instead of false confidence.
    with _step("9b: ambiguous_correct_detection"):
        classifier = MistakeClassifier()
        conn = get_db()
        correct_results = conn.execute(
            "SELECT * FROM question_results WHERE session_id=? AND is_correct=1",
            (session_id,),
        ).fetchall()
        for r in correct_results:
            qr = dict(r)
            is_ambiguous, reason = classifier.classify_ambiguous_correct(
                question_result=qr,
                time_spent_ms=qr["time_spent_ms"] or 0,
                answer_changes=qr["answer_change_count"] or 0,
                population_avg_time_secs=qr["population_avg_time_secs"],
                population_accuracy=qr["population_accuracy"],
                was_revisited=bool(qr["was_revisited"]),
                confidence_rating=qr["confidence_rating"],
                exam_target=exam_target,
            )
            conn.execute(
                "UPDATE question_results SET is_ambiguous_correct=?, ambiguous_reason=? WHERE id=?",
                (1 if is_ambiguous else 0, reason, qr["id"]),
            )
        conn.commit()
        conn.close()
    with _step("10: rank_leakage"):
        leakage = RankLeakageEngine().compute(session_id)
        conn = get_db()
        conn.execute(
            "UPDATE test_sessions SET leakage_report_json=? WHERE id=?",
            (json.dumps(leakage), session_id),
        )
        conn.commit()
        conn.close()

    # ── Step 11: Digital Twin update [Phase 4 — no-op hook] ────────────────
    with _step("11: digital_twin_update [Phase 4 stub]"):
        _digital_twin_update_hook(student_id, session_id)

    # ── Step 12: revision queue auto-create [Phase 7 — no-op hook] ─────────
    with _step("12: revision_queue [Phase 7 stub]"):
        _revision_queue_hook(student_id, session_id)

    # ── Step 13: update chapter_performance ────────────────────────────────
    with _step("13: chapter_performance"):
        conn = get_db()
        chapter_rows = conn.execute("""
            SELECT chapter_id,
                   SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct,
                   COUNT(*) as attempted
            FROM question_results
            WHERE session_id=? AND is_skipped=0
            GROUP BY chapter_id
        """, (session_id,)).fetchall()

        for row in chapter_rows:
            chapter_id = row["chapter_id"]
            existing = conn.execute(
                "SELECT total_attempted, total_correct FROM chapter_performance WHERE student_id=? AND chapter_id=?",
                (student_id, chapter_id),
            ).fetchone()
            if existing:
                new_attempted = existing["total_attempted"] + row["attempted"]
                new_correct = existing["total_correct"] + row["correct"]
            else:
                new_attempted = row["attempted"]
                new_correct = row["correct"]
            # accuracy is stored as a 0-1 fraction (e.g. 0.667), NOT 0-100.
            # This matches the contract analytics.py has always expected
            # (it does round(accuracy*100) when displaying), and matches the
            # column's own DDL comment/default in database.py. Storing this
            # as 0-100 here was the root cause of dashboard percentages
            # showing values like 6670% (66.7 stored, then *100 again on
            # display) -- fixed by keeping the single source of truth as a
            # fraction everywhere accuracy is stored.
            new_accuracy = round(new_correct / new_attempted, 4) if new_attempted else 0.0

            conn.execute("""
                INSERT INTO chapter_performance (student_id, chapter_id, accuracy, total_attempted, total_correct, last_updated)
                VALUES (?,?,?,?,?, datetime('now'))
                ON CONFLICT(student_id, chapter_id) DO UPDATE SET
                    accuracy=excluded.accuracy,
                    total_attempted=excluded.total_attempted,
                    total_correct=excluded.total_correct,
                    last_updated=excluded.last_updated
            """, (student_id, chapter_id, new_accuracy, new_attempted, new_correct))
        conn.commit()
        conn.close()

    # ── Step 14: population benchmark update — MUST run last ──────────────
    with _step("14: population_benchmarks"):
        PopulationBenchmarkUpdater().update_benchmarks(session_id)

    # ── Step 15: mark analysis ready ───────────────────────────────────────
    with _step("15: mark_analysis_ready"):
        conn = get_db()
        conn.execute("UPDATE test_sessions SET analysis_ready=1 WHERE id=?", (session_id,))
        conn.commit()
        conn.close()

    total_elapsed = time.monotonic() - pipeline_start
    logger.info("=== pipeline complete session_id=%s total=%.3fs ===", session_id, total_elapsed)
