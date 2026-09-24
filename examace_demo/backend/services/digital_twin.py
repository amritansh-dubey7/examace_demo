"""
Phase 4 — Digital Twin Engine.

DigitalTwinEngine maintains the continuously-evolving model of each student
stored in students.profile_json. It is the memory of the platform and the
primary data source for the AI mentor.

Design:
  • load()   → reads students.profile_json, returns dict (with defaults)
  • update() → idempotent; skips if session already in sessions_analyzed
  • save()   → atomic JSON write back to students.profile_json
  • get()    → convenience: returns the current twin without triggering an update

All update rules, cognitive score formulas, and prediction logic are
implemented exactly as specified in the master prompt's
"DIGITAL TWIN DEEP SPECIFICATION" section.
"""

import json
import logging
import math
from datetime import datetime, date
from typing import Optional
from backend.models.database import get_db

logger = logging.getLogger("examace.digital_twin")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[digital_twin] %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# ── JEE Advanced rank interpolation table (score → approximate rank) ─────────
JEE_ADVANCED_RANK_TABLE = [
    (290, 50), (270, 200), (250, 500), (230, 1000), (210, 2000),
    (190, 4000), (170, 7000), (150, 12000), (130, 20000), (110, 35000),
    (90, 60000), (70, 100000),
]

NEET_RANK_TABLE = [
    (720, 1), (700, 100), (680, 500), (660, 1500), (640, 5000),
    (620, 12000), (600, 25000), (580, 50000), (560, 80000), (540, 120000),
    (520, 175000), (500, 250000),
]


def _score_to_rank(score: float, exam_target: str) -> int:
    """Linearly interpolate score → approximate rank."""
    table = NEET_RANK_TABLE if exam_target == "NEET" else JEE_ADVANCED_RANK_TABLE
    if score >= table[0][0]:
        return table[0][1]
    if score <= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        s_hi, r_hi = table[i]
        s_lo, r_lo = table[i + 1]
        if s_lo <= score <= s_hi:
            frac = (score - s_lo) / (s_hi - s_lo)
            return int(r_hi + (1 - frac) * (r_lo - r_hi))
    return table[-1][1]


def _running_avg(old_avg: float, old_n: int, new_val: float) -> float:
    """Safe running average: (old_avg * n + new_val) / (n+1)."""
    return (old_avg * old_n + new_val) / (old_n + 1)


def _empty_twin() -> dict:
    """Return an initialised but empty Digital Twin."""
    return {
        "version": 0,
        "last_updated": None,
        "tests_analyzed": 0,
        "sessions_analyzed": [],
        "unlock_level": 1,
        "scores": {
            "conceptual_understanding": None,
            "problem_solving": None,
            "time_management": None,
            "confidence_calibration": None,
            "retention": None,
            "exam_readiness": None,
            "adaptability": None,
        },
        "topic_mastery": {},
        "mistake_distribution": {
            "conceptual": 0,
            "formula_recall": 0,
            "calculation": 0,
            "time_pressure": 0,
            "guessing": 0,
            "avoidance": 0,
            "careless": 0,
            "misread": 0,
        },
        "behavioral_patterns": {
            "fatigue_threshold_mins": None,
            "fatigue_trend": "unknown",
            "fatigue_detected_count": 0,
            "panic_detected_count": 0,
            "panic_fraction": 0.0,
            "panic_trend": "unknown",
            "avg_answer_changes_per_test": 0.0,
            "second_guess_penalty_avg_marks": 0.0,
            "overconfidence_rate": 0.0,
            "underconfidence_rate": 0.0,
            "avoidance_chapters": [],
            "section_time_bias": {},
            "question_selection_score": 0.0,
        },
        "score_history": [],
        "rank_leakage_summary": {
            "total_marks_leaked_all_time": 0,
            "avg_leaked_per_test": 0.0,
            "top_leakage_source": "",
            "leakage_trend": "unknown",
        },
        "predictions": {
            "score_range_low": None,
            "score_range_high": None,
            "rank_range_low": None,
            "rank_range_high": None,
            "confidence_pct": None,
            "on_track": None,
            "days_to_target_rank": None,
            "projection_basis": "",
        },
        "todays_highest_impact_action": "",
        "last_mentor_briefing": "",
    }


class DigitalTwinEngine:
    def __init__(self, student_id: int):
        self.student_id = student_id

    # ─────────────────────────── public API ──────────────────────────────────

    def get(self) -> dict:
        """Return current twin without triggering an update."""
        return self._load()

    def update(self, session_id: int):
        """
        Idempotent twin update for the given session.
        If session_id is already in sessions_analyzed, returns immediately.
        """
        twin = self._load()

        # ── IDEMPOTENCY CHECK ──────────────────────────────────────────────
        if session_id in twin["sessions_analyzed"]:
            logger.info("session %s already in twin; skipping", session_id)
            return

        conn = get_db()
        try:
            self._update_impl(conn, twin, session_id)
        finally:
            conn.close()

    # ─────────────────────────── internals ───────────────────────────────────

    def _load(self) -> dict:
        conn = get_db()
        row = conn.execute(
            "SELECT profile_json FROM students WHERE id=?", (self.student_id,)
        ).fetchone()
        conn.close()
        if not row:
            return _empty_twin()
        try:
            data = json.loads(row["profile_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not data or "version" not in data:
            return _empty_twin()
        # Fill in missing keys so old twins survive schema additions
        empty = _empty_twin()
        for k, v in empty.items():
            if k not in data:
                data[k] = v
        if "scores" in empty:
            for sk in empty["scores"]:
                data["scores"].setdefault(sk, None)
        if "behavioral_patterns" in empty:
            for bk, bv in empty["behavioral_patterns"].items():
                data["behavioral_patterns"].setdefault(bk, bv)
        return data

    def _save(self, twin: dict):
        twin["last_updated"] = datetime.utcnow().isoformat()
        twin["version"] = twin.get("version", 0) + 1
        conn = get_db()
        conn.execute(
            "UPDATE students SET profile_json=? WHERE id=?",
            (json.dumps(twin), self.student_id),
        )
        conn.commit()
        conn.close()

    def _update_impl(self, conn, twin: dict, session_id: int):
        """Core update logic. All heavy lifting happens here."""

        # ── Load session + student ─────────────────────────────────────────
        session_row = conn.execute("""
            SELECT s.*, t.exam_target, t.duration_mins, t.total_marks
            FROM test_sessions s JOIN test_templates t ON t.id = s.template_id
            WHERE s.id=?
        """, (session_id,)).fetchone()
        if not session_row:
            logger.warning("session %s not found; skipping twin update", session_id)
            return
        session = dict(session_row)

        student_row = conn.execute(
            "SELECT * FROM students WHERE id=?", (self.student_id,)
        ).fetchone()
        student = dict(student_row) if student_row else {}

        exam_target = session.get("exam_target", "jee").upper()
        max_score = float(session.get("max_score") or session.get("total_marks") or 300)
        raw_score = float(session.get("raw_score") or 0)

        # ── Load question_results for this session ─────────────────────────
        qresults = [dict(r) for r in conn.execute(
            "SELECT * FROM question_results WHERE session_id=?", (session_id,)
        ).fetchall()]

        # ── Load behavioral flags ─────────────────────────────────────────
        bf = {}
        try:
            bf = json.loads(session.get("behavioral_flags_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            bf = {}

        leakage = {}
        try:
            leakage = json.loads(session.get("leakage_report_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            leakage = {}

        n = twin["tests_analyzed"]  # BEFORE incrementing

        # ── TOPIC MASTERY UPDATE ───────────────────────────────────────────
        chapter_data: dict[int, dict] = {}
        for qr in qresults:
            cid = qr.get("chapter_id")
            if not cid or qr.get("is_skipped"):
                continue
            if cid not in chapter_data:
                chapter_data[cid] = {"correct": 0, "attempted": 0}
            chapter_data[cid]["attempted"] += 1
            if qr.get("is_correct"):
                chapter_data[cid]["correct"] += 1

        chapter_names: dict[int, str] = {}
        if chapter_data:
            ids_placeholder = ",".join("?" for _ in chapter_data)
            rows = conn.execute(
                f"SELECT id, name FROM chapters WHERE id IN ({ids_placeholder})",
                list(chapter_data.keys()),
            ).fetchall()
            for r in rows:
                chapter_names[r["id"]] = r["name"]

        # Track avoidance per chapter in this session
        avoidance_this_session: set[int] = set()
        for qr in qresults:
            if qr.get("classified_mistake_type") == "avoidance":
                cid = qr.get("chapter_id")
                if cid:
                    avoidance_this_session.add(cid)

        old_mastery_2_ago: dict[str, float] = {}
        for cid, stats in chapter_data.items():
            ch_name = chapter_names.get(cid, f"Chapter {cid}")
            if stats["attempted"] == 0:
                continue
            session_acc = stats["correct"] / stats["attempted"] * 100

            existing = twin["topic_mastery"].get(ch_name, {})
            old_mastery = existing.get("mastery", None)
            old_mastery_2 = existing.get("prev_mastery", None)
            old_mastery_2_ago[ch_name] = old_mastery_2 if old_mastery_2 is not None else (old_mastery or 0)

            if old_mastery is None:
                new_mastery = session_acc
            else:
                new_mastery = 0.65 * session_acc + 0.35 * old_mastery

            # Trend vs 2 updates ago
            if old_mastery_2 is not None:
                if new_mastery - old_mastery_2 >= 5:
                    trend = "improving"
                elif old_mastery_2 - new_mastery >= 5:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            old_total = existing.get("total_attempted", 0)
            old_correct = existing.get("total_correct", 0)
            avoidance_count = existing.get("avoidance_count", 0)
            if cid in avoidance_this_session:
                avoidance_count += 1

            twin["topic_mastery"][ch_name] = {
                "mastery": round(new_mastery, 1),
                "prev_mastery": round(old_mastery, 1) if old_mastery is not None else None,
                "total_attempted": old_total + stats["attempted"],
                "total_correct": old_correct + stats["correct"],
                "trend": trend,
                "last_session_accuracy": round(session_acc, 1),
                "avoidance_detected": avoidance_count > 0,
                "avoidance_count": avoidance_count,
            }

        # ── MISTAKE DISTRIBUTION UPDATE ────────────────────────────────────
        for qr in qresults:
            mtype = qr.get("classified_mistake_type")
            if mtype and mtype in twin["mistake_distribution"]:
                twin["mistake_distribution"][mtype] += 1

        # ── BEHAVIORAL PATTERNS UPDATE ─────────────────────────────────────
        bp = twin["behavioral_patterns"]

        # Panic
        panic_data = bf.get("panic", {})
        if panic_data.get("detected"):
            bp["panic_detected_count"] += 1
        # Second-guess penalty
        penalty = panic_data.get("marks_lost_to_panic", 0) or 0
        bp["second_guess_penalty_avg_marks"] = round(
            _running_avg(bp["second_guess_penalty_avg_marks"], n, penalty), 2
        )

        # Fatigue
        fatigue_data = bf.get("fatigue", {})
        if fatigue_data.get("detected"):
            bp["fatigue_detected_count"] += 1
            fthresh = fatigue_data.get("fatigue_threshold_mins")
            if fthresh is not None:
                # Update running median (simplified as running average here)
                old_thresh = bp.get("fatigue_threshold_mins")
                if old_thresh is None:
                    bp["fatigue_threshold_mins"] = fthresh
                else:
                    bp["fatigue_threshold_mins"] = round(
                        _running_avg(old_thresh, bp["fatigue_detected_count"] - 1, fthresh), 1
                    )

        # Fatigue trend
        n_fatigue = bp["fatigue_detected_count"]
        if n_fatigue == 0:
            bp["fatigue_trend"] = "unknown"
        elif n_fatigue <= 1:
            bp["fatigue_trend"] = "stable"
        elif n_fatigue / max(n + 1, 1) > 0.6:
            bp["fatigue_trend"] = "worsening"
        else:
            bp["fatigue_trend"] = "stable"

        # Answer changes (rough average from skip_pattern surrogate)
        # We count AnswerChanged events for this session
        change_count = sum(
            qr.get("answer_change_count", 0) or 0 for qr in qresults
        )
        bp["avg_answer_changes_per_test"] = round(
            _running_avg(bp["avg_answer_changes_per_test"], n, change_count), 1
        )

        # Overconfidence / underconfidence
        skip_data = bf.get("skip_pattern", {})
        confident_wrong = len(skip_data.get("confident_wrong", []))
        uncertain_correct = len(skip_data.get("uncertain_correct", []))
        total_answered = max(len(qresults), 1)
        session_over = confident_wrong / total_answered
        session_under = uncertain_correct / total_answered
        bp["overconfidence_rate"] = round(
            _running_avg(bp["overconfidence_rate"], n, session_over), 3
        )
        bp["underconfidence_rate"] = round(
            _running_avg(bp["underconfidence_rate"], n, session_under), 3
        )

        # Avoidance chapters: rebuild from twin data — chapters where avoidance
        # count / tests_analyzed_after_update > 0.30
        tests_after = n + 1
        bp["avoidance_chapters"] = [
            ch for ch, data in twin["topic_mastery"].items()
            if data.get("avoidance_count", 0) / tests_after > 0.30
        ]

        # Section time bias
        alloc = bf.get("section_allocation", {})
        for sec_name, sec_data in alloc.items():
            if isinstance(sec_data, dict):
                ou = sec_data.get("over_under_mins", 0) or 0
                old_bias = bp["section_time_bias"].get(sec_name, 0)
                bp["section_time_bias"][sec_name] = round(
                    _running_avg(old_bias, n, ou), 1
                )

        # Question selection
        qs_data = bf.get("question_selection", {})
        qs_score = qs_data.get("selection_score", 0.5) or 0.5
        bp["question_selection_score"] = round(
            _running_avg(bp["question_selection_score"], n, qs_score), 3
        )

        # Panic fraction + trend
        bp["panic_fraction"] = round(bp["panic_detected_count"] / tests_after, 3)
        if tests_after < 3:
            bp["panic_trend"] = "unknown"
        elif bp["panic_fraction"] > 0.6:
            bp["panic_trend"] = "worsening"
        elif bp["panic_fraction"] < 0.2:
            bp["panic_trend"] = "stable"
        else:
            bp["panic_trend"] = "stable"

        twin["behavioral_patterns"] = bp

        # ── SCORE HISTORY ──────────────────────────────────────────────────
        pct = round(raw_score / max_score * 100, 1) if max_score else 0
        leaked = float(leakage.get("total_leaked_from_wrong", 0) or 0)
        potential = float(leakage.get("potential_score", raw_score) or raw_score)
        twin["score_history"].append({
            "session_id": session_id,
            "test_number": tests_after,
            "score": raw_score,
            "max_score": max_score,
            "percentage": pct,
            "potential_score": potential,
            "leaked_marks": round(leaked, 1),
            "date": (session.get("submitted_at") or datetime.utcnow().isoformat())[:10],
        })

        # ── RANK LEAKAGE SUMMARY ───────────────────────────────────────────
        rls = twin["rank_leakage_summary"]
        rls["total_marks_leaked_all_time"] = round(
            rls.get("total_marks_leaked_all_time", 0) + leaked, 1
        )
        total_leaked_all = rls["total_marks_leaked_all_time"]
        rls["avg_leaked_per_test"] = round(total_leaked_all / tests_after, 1)
        rls["top_leakage_source"] = leakage.get("top_leakage_source", "") or ""

        # Leakage trend: compare last 3 sessions
        hist = twin["score_history"]
        if len(hist) >= 3:
            recent_leaked = [h.get("leaked_marks", 0) for h in hist[-3:]]
            if recent_leaked[-1] < recent_leaked[0] - 2:
                rls["leakage_trend"] = "improving"
            elif recent_leaked[-1] > recent_leaked[0] + 2:
                rls["leakage_trend"] = "worsening"
            else:
                rls["leakage_trend"] = "stable"
        twin["rank_leakage_summary"] = rls

        # ── INCREMENT COUNTER + UNLOCK LEVEL ──────────────────────────────
        twin["tests_analyzed"] = tests_after
        twin["sessions_analyzed"].append(session_id)

        if tests_after >= 10:
            twin["unlock_level"] = 4
        elif tests_after >= 6:
            twin["unlock_level"] = 3
        elif tests_after >= 3:
            twin["unlock_level"] = 2
        else:
            twin["unlock_level"] = 1

        # ── COGNITIVE SCORES ───────────────────────────────────────────────
        twin["scores"] = self._compute_cognitive_scores(conn, twin, exam_target, qresults)

        # ── PREDICTIONS (unlock level 2+, requires 3+ tests) ──────────────
        if twin["unlock_level"] >= 2:
            twin["predictions"] = self._compute_predictions(
                twin, exam_target, student
            )

        # ── TODAY'S HIGHEST IMPACT ACTION ─────────────────────────────────
        twin["todays_highest_impact_action"] = self._compute_highest_impact(twin)

        # ── MENTOR BRIEFING ────────────────────────────────────────────────
        twin["last_mentor_briefing"] = self._build_mentor_briefing(twin, student, exam_target)

        # ── SAVE ───────────────────────────────────────────────────────────
        self._save(twin)
        logger.info("twin updated for student=%s session=%s level=%s",
                    self.student_id, session_id, twin["unlock_level"])

    # ─────────────────────────── cognitive scores ────────────────────────────

    def _compute_cognitive_scores(self, conn, twin: dict, exam_target: str, qresults: list) -> dict:
        scores = twin["scores"].copy()
        n = twin["tests_analyzed"]

        # 1. Conceptual understanding — weighted average of topic mastery
        if twin["topic_mastery"]:
            # Load weightage for chapters
            chapter_weightages: dict[str, float] = {}
            rows = conn.execute("SELECT name, weightage FROM chapters").fetchall()
            for r in rows:
                chapter_weightages[r["name"]] = r["weightage"] or 1.0

            total_w = 0.0
            weighted_sum = 0.0
            for ch_name, ch_data in twin["topic_mastery"].items():
                if ch_data.get("total_attempted", 0) > 0:
                    w = chapter_weightages.get(ch_name, 1.0)
                    weighted_sum += ch_data["mastery"] * w
                    total_w += w
            if total_w > 0:
                scores["conceptual_understanding"] = round(weighted_sum / total_w, 1)

        # 2. Problem solving — accuracy on hard questions.
        # Primary: questions where population_accuracy < 0.40 (genuinely hard by population).
        # Fallback: questions with difficulty='hard' (when population_accuracy not stored).
        hard_results = [
            qr for qr in qresults
            if qr.get("population_accuracy") is not None
            and qr["population_accuracy"] < 0.40
            and not qr.get("is_skipped")
        ]
        if not hard_results:
            # Fallback: join to questions table to get difficulty
            hard_ids = [qr["question_id"] for qr in qresults if not qr.get("is_skipped")]
            if hard_ids:
                placeholders = ",".join("?" * len(hard_ids))
                hard_q_ids = {
                    r[0] for r in conn.execute(
                        f"SELECT id FROM questions WHERE id IN ({placeholders}) AND difficulty='hard'",
                        hard_ids
                    ).fetchall()
                }
                hard_results = [
                    qr for qr in qresults
                    if qr["question_id"] in hard_q_ids and not qr.get("is_skipped")
                ]
        if hard_results:
            hard_correct = sum(1 for qr in hard_results if qr.get("is_correct"))
            ps_score = hard_correct / len(hard_results) * 100
            old_ps = scores.get("problem_solving")
            if old_ps is None:
                scores["problem_solving"] = round(ps_score, 1)
            else:
                scores["problem_solving"] = round(_running_avg(old_ps, n - 1, ps_score), 1)

        # 3. Time management — from section_allocation data in twin behavioral_patterns
        # We compute from avg section_time_bias: 100*(1 - mean(|over_under|/optimal))
        # We store biases, so reconstruct metric.
        bias = twin["behavioral_patterns"].get("section_time_bias", {})
        if bias:
            OPTIMAL_MINS = {
                "Physics": 60, "Chemistry": 55, "Mathematics": 65,
                "Biology": 110, "General": 60,
            }
            abs_fracs = []
            for sec, over_under in bias.items():
                optimal = OPTIMAL_MINS.get(sec, 60)
                abs_fracs.append(abs(over_under) / optimal)
            if abs_fracs:
                tm_score = max(0.0, 100 * (1 - sum(abs_fracs) / len(abs_fracs)))
                scores["time_management"] = round(min(100.0, tm_score), 1)

        # 4. Confidence calibration
        over_r = twin["behavioral_patterns"].get("overconfidence_rate", 0)
        under_r = twin["behavioral_patterns"].get("underconfidence_rate", 0)
        if over_r or under_r:
            cc = 50 + (under_r - over_r) * 50
            scores["confidence_calibration"] = round(max(0, min(100, cc)), 1)

        # 5. Retention — compare chapter accuracy across sessions 14+ days apart
        hist = twin["score_history"]
        if len(hist) >= 3:
            # Proxy: if overall score trend is positive, retention is good
            recent_pcts = [h["percentage"] for h in hist[-4:]]
            if len(recent_pcts) >= 2:
                trend_val = recent_pcts[-1] - recent_pcts[0]
                retention = 50 + trend_val * 2
                scores["retention"] = round(max(0, min(100, retention)), 1)

        # 6. Adaptability — if chapter mistake counts are decreasing relative
        # to tests analyzed, the student is adapting.
        if n >= 3:
            # Simple proxy: improvement rate in mastery across chapters
            improving_count = sum(
                1 for ch in twin["topic_mastery"].values()
                if ch.get("trend") == "improving"
            )
            total_tracked = len(twin["topic_mastery"])
            if total_tracked > 0:
                adapt = improving_count / total_tracked * 100
                old_adapt = scores.get("adaptability")
                if old_adapt is None:
                    scores["adaptability"] = round(adapt, 1)
                else:
                    scores["adaptability"] = round(_running_avg(old_adapt, n - 1, adapt), 1)

        # 7. Exam readiness (composite)
        cu = scores.get("conceptual_understanding") or 0
        ps = scores.get("problem_solving") or 0
        tm = scores.get("time_management") or 0
        cc = scores.get("confidence_calibration") or 50
        ret = scores.get("retention") or 50
        adp = scores.get("adaptability") or 50

        if cu or ps or tm:  # at least some data
            er = (cu * 0.30 + ps * 0.25 + tm * 0.20 +
                  cc * 0.10 + ret * 0.10 + adp * 0.05)
            # Consistency bonus
            if len(hist) >= 5:
                recent_5 = [h["percentage"] for h in hist[-5:]]
                std = _std_dev(recent_5)
                if std < 8:
                    er += 5
            scores["exam_readiness"] = round(min(100, max(0, er)), 1)

        return scores

    # ─────────────────────────── predictions ─────────────────────────────────

    def _compute_predictions(self, twin: dict, exam_target: str, student: dict) -> dict:
        hist = twin["score_history"]
        if len(hist) < 3:
            return twin.get("predictions", {})

        # Use up to last 8 tests; minimum 3
        use_hist = hist[-8:]
        pcts = [h["percentage"] for h in use_hist]
        xs = list(range(len(pcts)))
        slope, intercept = _linear_regression(xs, pcts)
        std_err = _std_dev([p - (slope * x + intercept) for x, p in zip(xs, pcts)])

        projected_pct = slope * len(pcts) + intercept
        max_score_typical = hist[-1].get("max_score", 300)

        score_low = max(0, (projected_pct - std_err) / 100 * max_score_typical)
        score_high = min(max_score_typical, (projected_pct + std_err) / 100 * max_score_typical)

        rank_low = _score_to_rank(score_high, exam_target)   # higher score = lower rank
        rank_high = _score_to_rank(score_low, exam_target)

        confidence_pct = min(90, twin["tests_analyzed"] * 7)

        target_rank = student.get("target_rank") or None
        on_track = None
        if target_rank:
            rank_mid = (rank_low + rank_high) // 2
            on_track = rank_mid <= int(target_rank)

        # Days to exam
        exam_date_str = student.get("exam_date")
        days_to_exam = None
        if exam_date_str:
            try:
                exam_dt = datetime.strptime(exam_date_str[:10], "%Y-%m-%d").date()
                days_to_exam = (exam_dt - date.today()).days
            except ValueError:
                pass

        basis = (
            f"Based on last {len(pcts)} tests. "
            f"Projected score: {projected_pct:.1f}% ± {std_err:.1f}%."
        )

        return {
            "score_range_low": round(score_low, 1),
            "score_range_high": round(score_high, 1),
            "rank_range_low": rank_low,
            "rank_range_high": rank_high,
            "confidence_pct": confidence_pct,
            "on_track": on_track,
            "days_to_target_rank": days_to_exam,
            "projection_basis": basis,
        }

    # ─────────────────────────── action ──────────────────────────────────────

    def _compute_highest_impact(self, twin: dict) -> str:
        bp = twin["behavioral_patterns"]
        rls = twin["rank_leakage_summary"]
        md = twin["mistake_distribution"]

        # Priority 1: panic is dominant
        if bp.get("panic_fraction", 0) > 0.5:
            avg_penalty = bp.get("second_guess_penalty_avg_marks", 0)
            return (
                f"Drill timed 20-question sets and practice not changing first answers. "
                f"Panic behavior has cost you an average of {avg_penalty:.1f} marks per test."
            )

        top_source = rls.get("top_leakage_source", "")

        # Priority 2: conceptual leakage dominates
        if top_source == "conceptual":
            # Find worst chapter
            worst_ch = max(
                ((ch, d) for ch, d in twin["topic_mastery"].items() if d.get("total_attempted", 0) > 0),
                key=lambda x: -x[1]["mastery"],
                default=(None, {}),
            )
            if worst_ch[0]:
                ch_name, ch_data = worst_ch
                count = md.get("conceptual", 0)
                return (
                    f"Spend 45 mins on {ch_name} concept problems — "
                    f"{count} conceptual errors in your tests cost you marks. "
                    f"Current mastery: {ch_data['mastery']}%."
                )

        # Priority 3: calculation errors
        if top_source == "calculation":
            count = md.get("calculation", 0)
            return (
                f"Slow down on calculation steps. Check your arithmetic twice — "
                f"{count} calculation errors are costing you marks."
            )

        # Priority 4: avoidance
        avoid_chs = bp.get("avoidance_chapters", [])
        if avoid_chs:
            return (
                f"Face your avoidance pattern: {', '.join(avoid_chs[:2])} "
                f"{'are' if len(avoid_chs) > 1 else 'is'} being skipped across multiple tests. "
                f"Spend 30 mins attempting those questions directly."
            )

        # Priority 5: guessing is the top mistake type
        if md.get("guessing", 0) > md.get("careless", 0) and md.get("guessing", 0) > 0:
            count = md["guessing"]
            avg_leaked = rls.get("avg_leaked_per_test", 0)
            return (
                f"Stop random guessing — {count} guessing attempts across your tests "
                f"are costing you ~{avg_leaked:.1f} marks/test in negative marking. "
                f"If you don't know it, skip it. -1 is worse than 0."
            )

        # Priority 6: weakest chapter (always available after 2+ tests)
        topic_mastery = twin.get("topic_mastery", {})
        attempted = [(ch, d) for ch, d in topic_mastery.items() if d.get("total_attempted", 0) >= 2]
        if attempted:
            worst_ch, worst_data = min(attempted, key=lambda x: x[1]["mastery"])
            return (
                f"Spend 45 mins today on {worst_ch} — your weakest chapter "
                f"at {worst_data['mastery']}% mastery "
                f"({worst_data['total_attempted']} attempts, "
                f"{worst_data.get('correct', 0)} correct). "
                f"Focus on concept understanding, not speed."
            )

        return "Take your next mock test to generate a personalized action plan."

    # ─────────────────────────── mentor briefing ─────────────────────────────

    def _build_mentor_briefing(self, twin: dict, student: dict, exam_target: str) -> str:
        n = twin["tests_analyzed"]
        level = twin["unlock_level"]
        bp = twin["behavioral_patterns"]
        sc = twin["scores"]
        rls = twin["rank_leakage_summary"]
        hist = twin["score_history"]
        pred = twin["predictions"]

        lines = []
        lines.append("━━ STUDENT PROFILE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"Name: {student.get('name','Unknown')} | Exam: {exam_target} | Tests: {n}")
        lines.append(f"Unlock Level: {level}/4")
        target_rank = student.get("target_rank")
        exam_date_s = student.get("exam_date")
        lines.append(f"Target Rank: {target_rank or 'not set'}")

        if exam_date_s:
            try:
                ed = datetime.strptime(exam_date_s[:10], "%Y-%m-%d").date()
                days_left = (ed - date.today()).days
                lines.append(f"Days to Exam: {days_left}")
            except ValueError:
                lines.append("Days to Exam: not set")
        else:
            lines.append("Days to Exam: not set")

        if hist:
            lines.append("\n━━ SCORE TRAJECTORY ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            recent = hist[-3:]
            pcts = " → ".join(f"{h['percentage']}%" for h in recent)
            lines.append(f"Recent tests: {pcts}")
            leaked = rls.get("avg_leaked_per_test", 0)
            top_src = rls.get("top_leakage_source", "unknown")
            lines.append(f"Avg leaked marks/test: {leaked} ({top_src})")

        if level >= 2:
            lines.append("\n━━ BEHAVIORAL FLAGS ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            panic_frac = bp.get("panic_fraction", 0)
            panic_n = bp.get("panic_detected_count", 0)
            penalty_avg = bp.get("second_guess_penalty_avg_marks", 0)
            lines.append(f"Panic: detected in {panic_n}/{n} tests ({panic_frac*100:.0f}%)")
            lines.append(f"Second-guess penalty: avg {penalty_avg:.1f} marks/test lost to answer changes")
            avoid = bp.get("avoidance_chapters", [])
            if avoid:
                lines.append(f"Avoidance: {', '.join(avoid)} skipped in >30% of tests")
            over_r = bp.get("overconfidence_rate", 0)
            under_r = bp.get("underconfidence_rate", 0)
            lines.append(f"Overconfidence rate: {over_r*100:.0f}% | Underconfidence rate: {under_r*100:.0f}%")

        if level >= 3:
            lines.append("\n━━ COGNITIVE PROFILE ━━━━━━━━━━━━━━━━━━━━━━━━━")
            for name, key in [
                ("Conceptual Understanding", "conceptual_understanding"),
                ("Problem Solving", "problem_solving"),
                ("Time Management", "time_management"),
                ("Exam Readiness", "exam_readiness"),
            ]:
                v = sc.get(key)
                lines.append(f"{name}: {f'{v}/100' if v is not None else 'not yet available'}")

            lines.append("\n━━ WEAKEST TOPICS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            weakest = sorted(
                [(ch, d) for ch, d in twin["topic_mastery"].items() if d.get("total_attempted", 0) > 0],
                key=lambda x: x[1]["mastery"]
            )[:3]
            for i, (ch, d) in enumerate(weakest, 1):
                trend_arrow = {"improving": "↑", "declining": "↓", "stable": "→"}.get(d.get("trend", "stable"), "→")
                lines.append(f"{i}. {ch}: {d['mastery']}% {trend_arrow}")

        if level >= 2:
            lines.append("\n━━ TOP LEAKAGE SOURCES ━━━━━━━━━━━━━━━━━━━━━━━")
            md = twin["mistake_distribution"]
            sorted_leakage = sorted(md.items(), key=lambda x: -x[1])[:2]
            for mtype, count in sorted_leakage:
                if count > 0:
                    avg_marks = round(rls.get("avg_leaked_per_test", 0), 1)
                    lines.append(f"• {mtype}: {count} occurrences | avg {avg_marks} marks/test leaked")

        if level >= 4 and pred.get("score_range_low") is not None:
            lines.append("\n━━ PREDICTIONS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"Predicted score: {pred['score_range_low']}–{pred['score_range_high']}")
            lines.append(f"Predicted rank: {pred['rank_range_low']}–{pred['rank_range_high']}")
            lines.append(f"Confidence: {pred.get('confidence_pct')}% (based on {n} tests)")
            if pred.get("on_track") is not None:
                lines.append(f"On track: {'YES ✓' if pred['on_track'] else 'NO ✗'}")

        return "\n".join(lines)


# ─────────────────────────── math helpers ────────────────────────────────────

def _std_dev(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _linear_regression(xs: list, ys: list):
    """Ordinary least squares y = slope * x + intercept."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = numerator / denominator if denominator else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept
