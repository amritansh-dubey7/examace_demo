"""
Phase 3 — Behavioral Analysis Engine.

Implements, exactly per the master prompt's Behavioral Analysis Engine Deep
Specification (the section before Phase 1 in the master prompt):

  EventAnalyzer            reads raw test_events, computes structured metrics,
                            does NOT interpret them.
  MistakeClassifier         turns "wrong" into one of 8 specific categories,
                            in strict priority order, each with a confidence score.
  RankLeakageEngine          quantifies marks lost to each mistake category and
                            generates a "fix this first" recommendation.
  PopulationBenchmarkUpdater running-average updates to questions.population_*,
                            always the LAST step so it never affects the
                            classification of the test that just ran.

None of these classes touch the Digital Twin (students.profile_json) or the
revision queue — those are Phase 4 and Phase 7 respectively. This file only
produces per-session behavioral data; backend/services/pipeline.py wires it
into test_sessions / question_results / chapter_performance.
"""

import json
import statistics
from collections import defaultdict
from backend.models.database import get_db


# ── Optimal section time allocation (minutes), per exam_target ───────────────
# Keys must match test_templates.exam_target and the section_name values used
# by template_questions (seeded as "Physics" / "Chemistry" / "Mathematics" /
# "Biology" in question_seeder.py).
OPTIMAL_SECTION_ALLOCATION = {
    "JEE_ADVANCED": {"Physics": 60, "Chemistry": 55, "Mathematics": 65},
    "JEE_MAIN":     {"Physics": 60, "Chemistry": 60, "Mathematics": 60},
    "JEE":          {"Physics": 60, "Chemistry": 60, "Mathematics": 60},  # generic JEE templates fall back to Main split
    "NEET":         {"Physics": 45, "Chemistry": 45, "Biology": 110},
}


class EventAnalyzer:
    """
    Reads test_events + question_results for one session. All methods are
    pure computation over the raw event log — no judgement calls, no
    classification. MistakeClassifier is where interpretation happens.
    """

    def __init__(self, session_id: int):
        self.session_id = session_id
        self._events_cache = None
        self._session_cache = None

    # ── internal helpers ──────────────────────────────────────────────────
    def _session(self):
        if self._session_cache is None:
            conn = get_db()
            row = conn.execute("""
                SELECT s.*, t.exam_target, t.duration_mins, t.test_type
                FROM test_sessions s JOIN test_templates t ON t.id = s.template_id
                WHERE s.id=?
            """, (self.session_id,)).fetchone()
            conn.close()
            self._session_cache = dict(row) if row else {}
        return self._session_cache

    def _events(self):
        if self._events_cache is None:
            conn = get_db()
            rows = conn.execute("""
                SELECT question_id, event_type, event_data, occurred_at
                FROM test_events WHERE session_id=? ORDER BY occurred_at ASC
            """, (self.session_id,)).fetchall()
            conn.close()
            self._events_cache = [
                {
                    "question_id": r["question_id"],
                    "event_type": r["event_type"],
                    "event_data": json.loads(r["event_data"] or "{}"),
                    "occurred_at": r["occurred_at"],
                }
                for r in rows
            ]
        return self._events_cache

    def _test_start_ms(self):
        """
        First event's occurred_at is used as the operational test-start
        timestamp (client clock). started_at in test_sessions is a server
        TEXT datetime and not directly comparable to the client's epoch-ms
        occurred_at values, so we anchor all "elapsed since start" math to
        the first event instead.
        """
        events = self._events()
        return events[0]["occurred_at"] if events else 0

    def _background_intervals(self):
        """List of (start_ms, end_ms) the app was backgrounded, from
        AppBackgrounded/AppForegrounded event pairs."""
        intervals = []
        open_start = None
        events = self._events()
        
        submitted_at = None
        for e in events:
            if e["event_type"] == "TestSubmitted":
                submitted_at = e["occurred_at"]
                break

        for e in events:
            if e["event_type"] == "AppBackgrounded":
                open_start = e["occurred_at"]
            elif e["event_type"] == "AppForegrounded" and open_start is not None:
                intervals.append((open_start, e["occurred_at"]))
                open_start = None
                
        if open_start is not None and submitted_at is not None and open_start < submitted_at:
            intervals.append((open_start, submitted_at))
            
        return intervals

    def _subtract_background_ms(self, start_ms, end_ms):
        """Subtract any backgrounded time that overlaps [start_ms, end_ms]."""
        if end_ms <= start_ms:
            return max(0, end_ms - start_ms)
        overlap = 0
        for bg_start, bg_end in self._background_intervals():
            lo = max(start_ms, bg_start)
            hi = min(end_ms, bg_end)
            if hi > lo:
                overlap += (hi - lo)
        return max(0, (end_ms - start_ms) - overlap)

    # ── public metrics ────────────────────────────────────────────────────
    def compute_time_per_question(self) -> dict:
        """
        {question_id: time_spent_ms}, background time excluded.
        QuestionLeft events already carry a client-computed time_spent_ms
        (Phase 2's EventManager subtracts background time client-side too),
        so we sum those directly. For the LAST question of the test (no
        QuestionLeft fired because the test was submitted instead), we
        derive time from TestSubmitted.occurred_at minus the last
        QuestionViewed/QuestionRevisited for that question, background-adjusted.
        """
        events = self._events()
        time_spent = defaultdict(int)
        last_viewed_at = {}
        seen_left_for = set()
        submitted_at = None

        for e in events:
            qid = e["question_id"]
            if e["event_type"] == "TestSubmitted":
                submitted_at = e["occurred_at"]
            if qid is None:
                continue
            if e["event_type"] in ("QuestionViewed", "QuestionRevisited"):
                last_viewed_at[qid] = e["occurred_at"]
            elif e["event_type"] == "QuestionLeft":
                time_spent[qid] += int(e["event_data"].get("time_spent_ms", 0) or 0)
                seen_left_for.add(qid)

        # Any question that was viewed but never explicitly "left" (i.e. the
        # student was on it when the test ended) gets its tail time computed
        # from the gap to TestSubmitted.
        if submitted_at is not None:
            for qid, viewed_at in last_viewed_at.items():
                if qid not in seen_left_for:
                    tail = self._subtract_background_ms(viewed_at, submitted_at)
                    time_spent[qid] += tail

        return dict(time_spent)

    def compute_answer_change_log(self) -> dict:
        """{question_id: [{from, to, elapsed_ms_since_test_start}]}"""
        start_ms = self._test_start_ms()
        log = defaultdict(list)
        for e in self._events():
            if e["event_type"] == "AnswerChanged" and e["question_id"] is not None:
                log[e["question_id"]].append({
                    "from": e["event_data"].get("old_answer"),
                    "to": e["event_data"].get("new_answer"),
                    "elapsed_ms_since_test_start": max(0, e["occurred_at"] - start_ms),
                })
        return dict(log)

    def compute_fatigue_curve(self, test_duration_mins: int) -> list:
        """
        30-minute windows across the test. For each window: accuracy,
        avg_time_secs, question_count, and a difficulty-adjusted accuracy
        that removes the confound of "harder questions later = looks like
        fatigue when it's just difficulty."
        """
        start_ms = self._test_start_ms()
        time_per_q = self.compute_time_per_question()

        # Map question_id -> occurred_at of its QuestionLeft (or last view, as
        # a fallback) so we can bucket it into a 30-min window.
        last_left_at = {}
        for e in self._events():
            if e["event_type"] == "QuestionLeft" and e["question_id"] is not None:
                last_left_at[e["question_id"]] = e["occurred_at"]

        conn = get_db()
        results = conn.execute("""
            SELECT question_id, is_correct, difficulty_score
            FROM question_results qr
            JOIN questions q ON q.id = qr.question_id
            WHERE qr.session_id=?
        """, (self.session_id,)).fetchall()
        conn.close()
        result_by_q = {r["question_id"]: r for r in results}

        windows = []
        n_windows = max(1, -(-test_duration_mins // 30))  # ceil division
        for w in range(n_windows):
            w_start = w * 30
            w_end = min((w + 1) * 30, test_duration_mins)
            w_start_ms = start_ms + w_start * 60_000
            w_end_ms = start_ms + w_end * 60_000

            qids_in_window = [
                qid for qid, left_at in last_left_at.items()
                if w_start_ms <= left_at < w_end_ms
            ]
            if not qids_in_window:
                windows.append({
                    "window_start_mins": w_start, "window_end_mins": w_end,
                    "accuracy": None, "avg_time_secs": None,
                    "question_count": 0, "difficulty_adjusted_accuracy": None,
                })
                continue

            correct = sum(1 for qid in qids_in_window if result_by_q.get(qid) and result_by_q[qid]["is_correct"])
            accuracy = round(correct / len(qids_in_window) * 100, 1)
            times = [time_per_q.get(qid, 0) / 1000 for qid in qids_in_window if time_per_q.get(qid)]
            avg_time = round(statistics.mean(times), 1) if times else None

            # Difficulty-adjusted: each question's "expected" pass rate is its
            # difficulty_score (seeded from DIFFICULTY_BENCHMARKS, e.g. easy=0.85,
            # medium=0.50, hard=0.15 — see question_seeder.py). Adjusted accuracy
            # compares actual correctness to that expectation, then re-centers to
            # a 0-100 scale so a "tougher mix this window" doesn't read as fatigue.
            expected = [
                result_by_q[qid]["difficulty_score"] or 0.5
                for qid in qids_in_window if result_by_q.get(qid)
            ]
            if expected:
                avg_expected = statistics.mean(expected)
                # ratio > 1 means outperforming expectation for this difficulty mix
                ratio = (correct / len(qids_in_window)) / avg_expected if avg_expected > 0 else 1.0
                difficulty_adjusted = round(min(100, ratio * 50), 1)  # 50 = "met expectation" anchor
            else:
                difficulty_adjusted = accuracy

            windows.append({
                "window_start_mins": w_start, "window_end_mins": w_end,
                "accuracy": accuracy, "avg_time_secs": avg_time,
                "question_count": len(qids_in_window),
                "difficulty_adjusted_accuracy": difficulty_adjusted,
            })

        return windows

    def detect_fatigue(self, fatigue_curve: list) -> dict:
        """
        Fatigue if difficulty_adjusted_accuracy drops >15 points from peak
        AND the drop is sustained (the window after the peak, and every
        window after that up to the end, stays below peak - 15).
        """
        valid = [w for w in fatigue_curve if w["difficulty_adjusted_accuracy"] is not None]
        if len(valid) < 2:
            return {"detected": False, "fatigue_threshold_mins": None, "peak_accuracy": None}

        peak_idx = max(range(len(valid)), key=lambda i: valid[i]["difficulty_adjusted_accuracy"])
        peak_val = valid[peak_idx]["difficulty_adjusted_accuracy"]

        sustained_drop = True
        threshold_mins = None
        for w in valid[peak_idx + 1:]:
            if peak_val - w["difficulty_adjusted_accuracy"] <= 15:
                sustained_drop = False
                break
            if threshold_mins is None:
                threshold_mins = w["window_start_mins"]

        detected = sustained_drop and threshold_mins is not None and peak_idx < len(valid) - 1
        return {
            "detected": detected,
            "fatigue_threshold_mins": threshold_mins if detected else None,
            "peak_accuracy": peak_val,
        }

    def detect_panic(self, test_duration_mins: int) -> dict:
        """
        panic_window_start = 80% through the test. Compares the rate of
        AnswerChanged events per minute inside vs outside that window.
        """
        start_ms = self._test_start_ms()
        panic_window_start_mins = test_duration_mins * 0.8
        panic_start_ms = start_ms + panic_window_start_mins * 60_000
        end_ms = start_ms + test_duration_mins * 60_000

        normal_changes = 0
        panic_changes = 0
        for e in self._events():
            if e["event_type"] != "AnswerChanged":
                continue
            if e["occurred_at"] >= panic_start_ms:
                panic_changes += 1
            else:
                normal_changes += 1

        normal_duration_mins = max(0.01, panic_window_start_mins)
        panic_duration_mins = max(0.01, test_duration_mins - panic_window_start_mins)
        # Impose a baseline rate floor of 0.05 changes/min (1 change per 20 mins)
        # to prevent division-by-zero/trivialized ratios when normal_changes is 0
        normal_rate = max(0.05, normal_changes / normal_duration_mins)
        panic_rate = panic_changes / panic_duration_mins

        # Require panic_changes >= 3 to filter out strategic/methodical reviews
        detected = panic_rate > normal_rate * 2.5 and panic_changes >= 3

        # correct -> wrong changes inside the panic window
        conn = get_db()
        results = {
            r["question_id"]: dict(r)
            for r in conn.execute(
                "SELECT question_id, is_correct, marks_awarded FROM question_results WHERE session_id=?",
                (self.session_id,),
            ).fetchall()
        }
        questions = {
            r["id"]: dict(r)
            for r in conn.execute("""
                SELECT q.id, q.correct_answer, q.marks_correct FROM questions q
                JOIN question_results qr ON qr.question_id = q.id
                WHERE qr.session_id=?
            """, (self.session_id,)).fetchall()
        }
        conn.close()

        change_log = self.compute_answer_change_log()
        correct_changed_to_wrong = 0
        marks_lost_to_panic = 0.0
        for qid, changes in change_log.items():
            if qid not in questions:
                continue
            correct_answer = (questions[qid]["correct_answer"] or "").strip().upper()
            panic_changes_for_q = [
                c for c in changes
                if (start_ms + c["elapsed_ms_since_test_start"]) >= panic_start_ms
            ]
            for c in panic_changes_for_q:
                old = (c["from"] or "").strip().upper()
                new = (c["to"] or "").strip().upper()
                if old == correct_answer and new != correct_answer:
                    correct_changed_to_wrong += 1
                    marks_lost_to_panic += abs(questions[qid]["marks_correct"] or 4.0)

        session = self._session()
        self_reported_panic = session.get("final_30min_feeling") == "panicked"
        confidence = 0.81 if detected else 0.3
        if detected and self_reported_panic:
            confidence = min(0.95, confidence + 0.09)

        return {
            "detected": detected,
            "panic_window_start_mins": round(panic_window_start_mins, 1),
            "normal_change_rate_per_min": round(normal_rate, 3),
            "panic_change_rate_per_min": round(panic_rate, 3),
            "correct_changed_to_wrong": correct_changed_to_wrong,
            "marks_lost_to_panic": round(marks_lost_to_panic, 2),
            "confidence": round(confidence, 2),
        }

    def get_panic_window_question_ids(self, test_duration_mins: int) -> set:
        """Question IDs whose final QuestionLeft (or last viewed if never
        left) falls inside the panic window — used to set in_panic_window."""
        start_ms = self._test_start_ms()
        panic_start_ms = start_ms + (test_duration_mins * 0.8) * 60_000
        qids = set()
        last_activity = {}
        for e in self._events():
            qid = e["question_id"]
            if qid is None:
                continue
            if e["event_type"] in ("QuestionLeft", "AnswerSelected", "AnswerChanged"):
                last_activity[qid] = e["occurred_at"]
        for qid, ts in last_activity.items():
            if ts >= panic_start_ms:
                qids.add(qid)
        return qids

    def compute_skip_pattern(self) -> dict:
        """
        sight_skipped:      viewed < 10s total, not answered
        attempt_abandoned:  viewed > 30s total, not answered
        confident:          answered within 30s of first view, no changes, no revisit
        uncertain:          viewed 2+ times OR changed 1+ times
        Cross-referenced against is_correct from question_results for the
        confident_correct/confident_wrong/uncertain_correct/uncertain_wrong split.
        """
        time_per_q = self.compute_time_per_question()
        events = self._events()

        view_counts = defaultdict(int)
        first_viewed_at = {}
        first_answered_at = {}
        change_counts = defaultdict(int)
        answered_qids = set()

        for e in events:
            qid = e["question_id"]
            if qid is None:
                continue
            if e["event_type"] in ("QuestionViewed", "QuestionRevisited"):
                view_counts[qid] += 1
                if qid not in first_viewed_at:
                    first_viewed_at[qid] = e["occurred_at"]
            elif e["event_type"] == "AnswerSelected":
                answered_qids.add(qid)
                if qid not in first_answered_at:
                    first_answered_at[qid] = e["occurred_at"]
            elif e["event_type"] == "AnswerChanged":
                change_counts[qid] += 1

        conn = get_db()
        rows = conn.execute(
            "SELECT question_id, is_correct, is_skipped FROM question_results WHERE session_id=?",
            (self.session_id,),
        ).fetchall()
        conn.close()

        sight_skipped, attempt_abandoned = [], []
        confident_correct, confident_wrong = [], []
        uncertain_correct, uncertain_wrong = [], []

        for r in rows:
            qid = r["question_id"]
            total_time = time_per_q.get(qid, 0)
            revisited = view_counts.get(qid, 0) >= 2
            changed = change_counts.get(qid, 0) >= 1

            if r["is_skipped"]:
                if total_time < 10_000:
                    sight_skipped.append(qid)
                elif total_time > 30_000:
                    attempt_abandoned.append(qid)
                continue

            is_fast_single_pass = (
                qid in first_answered_at and qid in first_viewed_at
                and (first_answered_at[qid] - first_viewed_at[qid]) <= 30_000
                and not changed and not revisited
            )

            if is_fast_single_pass:
                (confident_correct if r["is_correct"] else confident_wrong).append(qid)
            elif revisited or changed:
                (uncertain_correct if r["is_correct"] else uncertain_wrong).append(qid)

        return {
            "sight_skipped": sight_skipped,
            "attempt_abandoned": attempt_abandoned,
            "confident_correct": confident_correct,
            "confident_wrong": confident_wrong,
            "uncertain_correct": uncertain_correct,
            "uncertain_wrong": uncertain_wrong,
        }

    def compute_section_allocation(self, exam_target: str) -> dict:
        """{section_name: {actual_mins, optimal_mins, over_under_mins, accuracy}}"""
        session = self._session()
        test_type = (session.get("test_type") or "").upper()
        optimal_key = exam_target
        if exam_target == "JEE" and "ADVANCED" in test_type:
            optimal_key = "JEE_ADVANCED"
        elif exam_target == "JEE":
            optimal_key = "JEE_MAIN"
        optimal = OPTIMAL_SECTION_ALLOCATION.get(optimal_key, OPTIMAL_SECTION_ALLOCATION.get(exam_target, {}))

        # Primary method: SectionChanged events + TestSubmitted tail
        section_events = [e for e in self._events() if e["event_type"] in ("SectionChanged", "TestSubmitted")]
        section_ms = defaultdict(int)
        current_section = None
        current_start = None

        conn = get_db()
        first_section_row = conn.execute("""
            SELECT te.question_id, q.subject_id, s.name as section_name, te.occurred_at
            FROM test_events te
            JOIN questions q ON q.id = te.question_id
            JOIN subjects s ON s.id = q.subject_id
            WHERE te.session_id=? AND te.event_type='QuestionViewed'
            ORDER BY te.occurred_at ASC LIMIT 1
        """, (self.session_id,)).fetchone()
        conn.close()

        if first_section_row:
            current_section = first_section_row["section_name"]
            current_start = first_section_row["occurred_at"]

        for e in section_events:
            if e["event_type"] == "SectionChanged":
                to_section = e["event_data"].get("to_section")
                if current_section is not None and current_start is not None:
                    section_ms[current_section] += self._subtract_background_ms(current_start, e["occurred_at"])
                current_section = to_section
                current_start = e["occurred_at"]
            elif e["event_type"] == "TestSubmitted" and current_section is not None and current_start is not None:
                section_ms[current_section] += self._subtract_background_ms(current_start, e["occurred_at"])
                current_section = None

        # Fallback: if no SectionChanged events fired (linear test, no switching),
        # sum time_spent_ms from question_results grouped by subject — always available.
        if not any(section_ms.values()):
            conn = get_db()
            time_rows = conn.execute("""
                SELECT s.name as section_name, SUM(qr.time_spent_ms) as total_ms
                FROM question_results qr
                JOIN subjects s ON s.id = qr.subject_id
                WHERE qr.session_id=? AND qr.time_spent_ms IS NOT NULL
                GROUP BY s.name
            """, (self.session_id,)).fetchall()
            conn.close()
            for r in time_rows:
                if r["total_ms"]:
                    section_ms[r["section_name"]] = int(r["total_ms"])

        conn = get_db()
        acc_rows = conn.execute("""
            SELECT s.name as section_name,
                   AVG(CASE WHEN qr.is_correct THEN 1.0 ELSE 0.0 END) * 100 as accuracy
            FROM question_results qr
            JOIN subjects s ON s.id = qr.subject_id
            WHERE qr.session_id=? AND qr.is_skipped=0
            GROUP BY s.name
        """, (self.session_id,)).fetchall()
        conn.close()
        accuracy_by_section = {r["section_name"]: round(r["accuracy"], 1) for r in acc_rows}

        result = {}
        all_sections = set(section_ms.keys()) | set(optimal.keys()) | set(accuracy_by_section.keys())
        for section in all_sections:
            actual_mins = round(section_ms.get(section, 0) / 60_000, 1)
            optimal_mins = optimal.get(section)
            over_under = round(actual_mins - optimal_mins, 1) if optimal_mins is not None else None
            result[section] = {
                "actual_mins": actual_mins,
                "optimal_mins": optimal_mins,
                "over_under_mins": over_under,
                "accuracy": accuracy_by_section.get(section),
            }
        return result

    def compute_question_selection_quality(self) -> dict:
        """
        Ideal strategy: attempt all easy, most medium, hard only if time
        permits. Penalizes attempting hard questions while skipping medium ones.
        """
        conn = get_db()
        rows = conn.execute("""
            SELECT q.difficulty, qr.is_skipped
            FROM question_results qr JOIN questions q ON q.id = qr.question_id
            WHERE qr.session_id=?
        """, (self.session_id,)).fetchall()
        conn.close()

        counts = {
            "easy_attempted": 0, "easy_skipped": 0,
            "medium_attempted": 0, "medium_skipped": 0,
            "hard_attempted": 0, "hard_skipped": 0,
        }
        for r in rows:
            key = f"{r['difficulty']}_{'skipped' if r['is_skipped'] else 'attempted'}"
            if key in counts:
                counts[key] += 1

        easy_total = counts["easy_attempted"] + counts["easy_skipped"]
        medium_total = counts["medium_attempted"] + counts["medium_skipped"]

        easy_rate = counts["easy_attempted"] / easy_total if easy_total else 1.0
        medium_rate = counts["medium_attempted"] / medium_total if medium_total else 1.0

        # Penalize attempting hard questions while skipping medium ones —
        # this is the "poor selection" pattern called out in the spec.
        poor_selection_penalty = 0.0
        if counts["hard_attempted"] > 0 and counts["medium_skipped"] > 0:
            poor_selection_penalty = min(0.4, 0.1 * min(counts["hard_attempted"], counts["medium_skipped"]))

        selection_score = round(max(0.0, min(1.0, (easy_rate * 0.5 + medium_rate * 0.5) - poor_selection_penalty)), 2)

        if poor_selection_penalty > 0:
            advice = (
                f"You attempted {counts['hard_attempted']} hard question(s) while skipping "
                f"{counts['medium_skipped']} medium question(s) — medium questions are usually "
                f"better marks-per-minute. Clear all medium questions before attempting hard ones."
            )
        elif easy_rate < 1.0:
            advice = f"You skipped {counts['easy_skipped']} easy question(s) — these should almost always be attempted first."
        else:
            advice = "Good question selection — easy and medium questions were prioritized appropriately."

        return {"selection_score": selection_score, **counts, "advice": advice}


class MistakeClassifier:
    """
    Turns "wrong" into one of 8 categories, in strict priority order
    (first matching rule wins), each with a fixed confidence score per
    the master prompt's accuracy table.
    """

    def classify(self, question_result: dict, time_spent_ms: int, answer_changes: int,
                 answer_change_log: list, population_avg_time_secs, population_accuracy,
                 was_revisited: bool, confidence_rating, in_panic_window: bool,
                 exam_target: str = "JEE"):
        is_correct = bool(question_result.get("is_correct"))
        is_skipped = bool(question_result.get("is_skipped"))

        pop_time_secs = population_avg_time_secs or 120
        time_ratio = (time_spent_ms / 1000.0) / pop_time_secs if pop_time_secs else 1.0
        pop_accuracy = population_accuracy if population_accuracy is not None else 0.5

        # Rule 1 — AVOIDANCE
        if is_skipped:
            # is_skipped=True already implies a question_result row exists;
            # per the data model that only happens for questions in the
            # template, and Phase 2 always fires QuestionViewed when a
            # question is shown, so a skipped question that was the current
            # question at some point was definitionally viewed. We treat all
            # skips as avoidance with high confidence, matching the spec's intent.
            return "avoidance", 0.92

        if is_correct:
            # Classifier is only meaningful for wrong/skipped answers (see
            # pipeline Step 9: "where is_correct=0 OR is_skipped=1"). Guard
            # here too in case classify() is ever called directly.
            return None, 0.0

        # Calibrate limits dynamically by exam target
        target = (exam_target or "JEE").upper()
        if "ADVANCED" in target:
            guessing_limit = 0.25
            careless_limit = 0.6
            misread_limit = 0.4
            conceptual_limit = 1.4
            calculation_limit = 1.0
        elif "NEET" in target:
            guessing_limit = 0.30
            careless_limit = 0.7
            misread_limit = 0.5
            conceptual_limit = 1.3
            calculation_limit = 0.9
        else:
            # Default (JEE_MAIN, generic JEE)
            guessing_limit = 0.35
            careless_limit = 0.7
            misread_limit = 0.5
            conceptual_limit = 1.2
            calculation_limit = 0.9

        # Rule 2 — GUESSING
        # Triggered by objective speed signal (time_ratio well below threshold).
        # confidence_rating=="guessing" is a supporting signal only — a student can
        # mark "guessing" on a hard question they spent 3 minutes on, which is NOT
        # random guessing. We require both the speed signal AND the rating to avoid
        # mass over-classification of careful-but-unsure answers as guessing.
        pure_speed_guess = time_ratio < guessing_limit and answer_changes == 0
        rated_guessing   = confidence_rating == "guessing"
        if pure_speed_guess or (rated_guessing and time_ratio < (guessing_limit * 2.5)):
            return "guessing", 0.87

        # Rule 3 — TIME_PRESSURE
        if in_panic_window and answer_changes >= 1 and time_ratio < misread_limit:
            return "time_pressure", 0.81

        # Rule 8 — MISREAD (evaluated before Careless to resolve shadowing)
        if time_ratio < misread_limit and not was_revisited and confidence_rating in ("sure", "unsure") and pop_accuracy > 0.70:
            return "misread", 0.60

        # Rule 4 — CARELESS
        if (time_ratio < careless_limit and answer_changes == 0 and pop_accuracy > 0.65) or \
           (confidence_rating == "sure" and pop_accuracy > 0.55):
            return "careless", 0.79

        # Rule 5 — CONCEPTUAL
        if time_ratio > conceptual_limit and answer_changes >= 1 and was_revisited:
            return "conceptual", 0.76

        # Rule 6 — FORMULA_RECALL
        if 0.8 <= time_ratio <= (conceptual_limit + 0.2) and was_revisited and answer_changes <= 1:
            return "formula_recall", 0.70

        # Rule 7 — CALCULATION
        if time_ratio > calculation_limit and answer_changes >= 2 and pop_accuracy > 0.55:
            return "calculation", 0.65

        # Default
        return "conceptual", 0.50

    def classify_ambiguous_correct(self, question_result: dict, time_spent_ms: int,
                                    answer_changes: int, population_avg_time_secs,
                                    population_accuracy, was_revisited: bool,
                                    confidence_rating, exam_target: str = "JEE"):
        """
        Phase 7 — Flags CORRECT answers that show signs of being lucky rather
        than solidly understood, so the student doesn't mistake luck for
        mastery. Only called for is_correct=1 rows (separate from classify(),
        which only ever runs on wrong/skipped answers).

        Returns (is_ambiguous: bool, reason: str | None).

        Detection rules (any one match flags it — not stacked confidence,
        just a binary "this deserves a second look" flag):

          1. GUESSED_BUT_RIGHT — answered very fast (well below population
             average time) with zero answer changes and self-rated
             "guessing". Classic lucky guess.

          2. TOO_LONG_FOR_RIGHT — took far longer than the population average
             to arrive at the correct answer, with 2+ answer changes. Got
             there eventually but the process wasn't clean — conceptual
             clarity is shaky even though the final answer landed correctly.

          3. FLIP_FLOPPED_TO_CORRECT — multiple answer changes (3+) before
             landing on the correct option. Suggests elimination/trial-and-
             error rather than a confident, direct solve.
        """
        pop_time_secs = population_avg_time_secs or 120
        time_ratio = (time_spent_ms / 1000.0) / pop_time_secs if pop_time_secs else 1.0

        target = (exam_target or "JEE").upper()
        if "ADVANCED" in target:
            fast_limit, slow_limit = 0.25, 1.6
        elif "NEET" in target:
            fast_limit, slow_limit = 0.30, 1.5
        else:
            fast_limit, slow_limit = 0.35, 1.4

        # Rule 1 — guessed but right (fast + no deliberation + self-rated guessing)
        if time_ratio < fast_limit and answer_changes == 0 and confidence_rating == "guessing":
            return True, "guessed_but_correct"

        # Rule 2 — took unusually long to get there, with churn (shaky process)
        if time_ratio > slow_limit and answer_changes >= 2:
            return True, "too_long_for_right_answer"

        # Rule 3 — flip-flopped through multiple options before landing correct
        if answer_changes >= 3:
            return True, "flip_flopped_to_correct"

        return False, None


class RankLeakageEngine:
    """Quantifies marks lost to each mistake category and what could have
    been scored, then generates a single highest-priority recommendation."""

    def compute(self, session_id: int) -> dict:
        conn = get_db()
        session = conn.execute("SELECT raw_score, max_score FROM test_sessions WHERE id=?", (session_id,)).fetchone()
        rows = conn.execute("""
            SELECT qr.*, q.marks_correct, q.marks_incorrect, ch.name as chapter_name
            FROM question_results qr
            JOIN questions q ON q.id = qr.question_id
            JOIN chapters ch ON ch.id = qr.chapter_id
            WHERE qr.session_id=?
        """, (session_id,)).fetchall()
        conn.close()

        actual_score = session["raw_score"] if session else 0.0
        total_leaked_from_wrong = 0.0
        total_opportunity_cost = 0.0
        breakdown = defaultdict(lambda: {"marks": 0.0, "count": 0, "chapters": defaultdict(float)})
        confidences = []

        for r in rows:
            mtype = r["classified_mistake_type"] or "unclassified"
            mconf = r["mistake_confidence"] or 0.0

            if r["is_skipped"]:
                opportunity_cost = r["marks_correct"] or 4.0
                total_opportunity_cost += opportunity_cost
                breakdown[mtype]["marks"] += opportunity_cost
                breakdown[mtype]["count"] += 1
                breakdown[mtype]["chapters"][r["chapter_name"]] += opportunity_cost
                confidences.append(mconf)
            elif not r["is_correct"]:
                marks_lost = abs(r["marks_incorrect"] or 1.0)
                total_leaked_from_wrong += marks_lost
                breakdown[mtype]["marks"] += marks_lost
                breakdown[mtype]["count"] += 1
                breakdown[mtype]["chapters"][r["chapter_name"]] += marks_lost
                confidences.append(mconf)

        potential_score = actual_score + total_leaked_from_wrong

        breakdown_out = {}
        for mtype, data in breakdown.items():
            top_chapters = sorted(data["chapters"].items(), key=lambda kv: -kv[1])[:3]
            breakdown_out[mtype] = {
                "marks": round(data["marks"], 2),
                "count": data["count"],
                "top_chapters": [c[0] for c in top_chapters],
            }

        top_leakage_source = max(breakdown_out.items(), key=lambda kv: kv[1]["marks"])[0] if breakdown_out else None

        behavioral_types = {"panic", "time_pressure", "guessing", "careless", "avoidance"}
        knowledge_types = {"conceptual", "formula_recall", "calculation", "misread"}

        fix_this_first = "Not enough mistakes recorded yet to generate a targeted recommendation."
        if top_leakage_source:
            data = breakdown_out[top_leakage_source]
            if top_leakage_source in behavioral_types:
                label_map = {
                    "time_pressure": "Stop changing answers under time pressure",
                    "guessing": "Stop guessing on unfamiliar questions",
                    "careless": "Slow down on easy questions",
                    "avoidance": "Stop skipping questions you could attempt",
                }
                headline = label_map.get(top_leakage_source, "Address a behavioral pattern")
                fix_this_first = f"{headline} — this cost you {data['marks']:.0f} marks this test."
            elif top_leakage_source in knowledge_types and data["top_chapters"]:
                chapters_str = ", ".join(data["top_chapters"])
                fix_this_first = (
                    f"Study {chapters_str} — {data['count']} {top_leakage_source} error(s) "
                    f"in this area cost you {data['marks']:.0f} marks."
                )
            else:
                fix_this_first = f"Focus on reducing {top_leakage_source} errors — {data['marks']:.0f} marks lost this test."

        leakage_confidence = round(statistics.mean(confidences), 2) if confidences else 0.0

        return {
            "actual_score": round(actual_score, 2),
            "potential_score": round(potential_score, 2),
            "total_leaked_from_wrong": round(total_leaked_from_wrong, 2),
            "total_opportunity_cost_from_skipped": round(total_opportunity_cost, 2),
            "breakdown": breakdown_out,
            "top_leakage_source": top_leakage_source,
            "fix_this_first": fix_this_first,
            "leakage_confidence": leakage_confidence,
        }


class PopulationBenchmarkUpdater:
    """Running-average update of questions.population_* fields. Must run
    LAST in the pipeline so benchmarks don't influence their own session's
    classification."""

    def update_benchmarks(self, session_id: int):
        conn = get_db()
        
        # Check if already applied to prevent double-counting
        row = conn.execute("SELECT benchmarks_applied FROM test_sessions WHERE id=?", (session_id,)).fetchone()
        if row and row["benchmarks_applied"]:
            conn.close()
            return

        rows = conn.execute(
            "SELECT question_id, is_correct, time_spent_ms FROM question_results WHERE session_id=? AND is_skipped=0",
            (session_id,),
        ).fetchall()

        for r in rows:
            q = conn.execute(
                "SELECT population_accuracy, population_avg_time_secs, population_sample_size FROM questions WHERE id=?",
                (r["question_id"],),
            ).fetchone()
            if not q:
                continue
            old_n = q["population_sample_size"] or 0
            old_acc = q["population_accuracy"] if q["population_accuracy"] is not None else 0.5
            old_time = q["population_avg_time_secs"] if q["population_avg_time_secs"] is not None else 120

            new_n = old_n + 1
            new_acc = (old_acc * old_n + (1.0 if r["is_correct"] else 0.0)) / new_n
            new_time = (old_time * old_n + (r["time_spent_ms"] / 1000.0)) / new_n

            conn.execute("""
                UPDATE questions
                SET population_accuracy=?, population_avg_time_secs=?, population_sample_size=?
                WHERE id=?
            """, (round(new_acc, 4), round(new_time, 1), new_n, r["question_id"]))

        conn.execute("UPDATE test_sessions SET benchmarks_applied=1 WHERE id=?", (session_id,))
        conn.commit()
        conn.close()
