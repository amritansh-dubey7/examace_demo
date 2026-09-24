import json
from collections import defaultdict
from backend.models.database import get_db

class AnalyticsService:
    def __init__(self, student_id: int):
        self.student_id = student_id

    def get_dashboard_stats(self):
        conn = get_db()
        c = conn.cursor()
        sid = self.student_id

        total_study = c.execute(
            "SELECT COALESCE(SUM(duration_mins),0) FROM study_sessions WHERE student_id=?", (sid,)
        ).fetchone()[0]

        # Combine BOTH sources of completed tests: manually-logged mock_tests
        # (the original Phase 1 feature) and live test_sessions taken through
        # the Phase 2 test interface. Without this UNION, every test taken
        # live was invisible to the dashboard -- "0 day streak" and "no
        # tests yet" even right after finishing a real test, because this
        # method only ever looked at mock_tests.
        all_tests = c.execute("""
            SELECT scored_marks, total_marks, date(date) as test_date FROM mock_tests WHERE student_id=?
            UNION ALL
            SELECT raw_score as scored_marks, max_score as total_marks, date(submitted_at) as test_date
            FROM test_sessions WHERE student_id=? AND status='submitted'
        """, (sid, sid)).fetchall()

        avg_score = 0
        valid_tests = [r for r in all_tests if r["total_marks"] and r["total_marks"] > 0]
        if valid_tests:
            avg_score = sum(r["scored_marks"] / r["total_marks"] * 100 for r in valid_tests) / len(valid_tests)

        streak = self._calc_streak()
        weak_topics = self._get_weak_topics()

        recent_tests = c.execute("""
            SELECT title, scored_marks, total_marks, date, session_id FROM (
                SELECT title, scored_marks, total_marks, date, NULL as session_id
                FROM mock_tests WHERE student_id=?
                UNION ALL
                SELECT t.name as title, s.raw_score as scored_marks, s.max_score as total_marks,
                       s.submitted_at as date, s.id as session_id
                FROM test_sessions s JOIN test_templates t ON t.id = s.template_id
                WHERE s.student_id=? AND s.status='submitted'
            ) combined
            ORDER BY date DESC LIMIT 5
        """, (sid, sid)).fetchall()

        study_by_day = c.execute("""
            SELECT date, SUM(duration_mins) as mins
            FROM study_sessions WHERE student_id=?
            GROUP BY date ORDER BY date DESC LIMIT 14
        """, (sid,)).fetchall()

        conn.close()
        return {
            "total_study_hours": round(total_study / 60, 1),
            "avg_test_score": round(avg_score, 1),
            "streak_days": streak,
            "weak_topics": weak_topics,
            "recent_tests": [dict(r) for r in recent_tests],
            "study_by_day": [dict(r) for r in study_by_day],
            "total_tests": len(all_tests),
        }

    def _calc_streak(self):
        conn = get_db()
        dates = [r[0] for r in conn.execute("""
            SELECT date FROM study_sessions WHERE student_id=?
            UNION
            SELECT date(submitted_at) FROM test_sessions WHERE student_id=? AND status='submitted'
            ORDER BY date DESC
        """, (self.student_id, self.student_id)).fetchall()]
        conn.close()
        if not dates:
            return 0
        from datetime import date, timedelta
        today = date.today()
        streak = 0
        for i, d in enumerate(dates):
            expected = str(today - timedelta(days=i))
            if d == expected:
                streak += 1
            else:
                break
        return streak

    def _get_weak_topics(self, limit=5):
        conn = get_db()
        rows = conn.execute("""
            SELECT cp.accuracy, cp.total_attempted, ch.name, s.name as subject
            FROM chapter_performance cp
            JOIN chapters ch ON ch.id = cp.chapter_id
            JOIN subjects s ON s.id = ch.subject_id
            WHERE cp.student_id=? AND cp.total_attempted > 0
            ORDER BY cp.accuracy ASC LIMIT ?
        """, (self.student_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_chapter_performance(self):
        conn = get_db()
        rows = conn.execute("""
            SELECT cp.*, ch.name as chapter_name, s.name as subject_name, s.color
            FROM chapter_performance cp
            JOIN chapters ch ON ch.id = cp.chapter_id
            JOIN subjects s ON s.id = ch.subject_id
            WHERE cp.student_id=?
            ORDER BY s.name, cp.accuracy
        """, (self.student_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_mistake_analysis(self):
        conn = get_db()
        # Legacy manually-logged mistakes (Phase 1 feature, typed in by hand)
        manual_rows = conn.execute("""
            SELECT m.id, m.error_type, m.question_text, m.date, m.resolved,
                   ch.name as chapter_name, s.name as subject_name,
                   NULL as mistake_confidence, NULL as session_id
            FROM mistakes m
            JOIN chapters ch ON ch.id = m.chapter_id
            JOIN subjects s ON s.id = ch.subject_id
            WHERE m.student_id=?
        """, (self.student_id,)).fetchall()

        # Mistakes auto-classified by the Phase 3 behavioral pipeline from
        # live test attempts -- without this, every wrong/skipped answer a
        # student makes in a live test is invisible here even though it's
        # fully classified and sitting in question_results.
        live_rows = conn.execute("""
            SELECT qr.id, qr.classified_mistake_type as error_type, q.question_text,
                   date(ts.submitted_at) as date, NULL as resolved,
                   ch.name as chapter_name, sub.name as subject_name,
                   qr.mistake_confidence, qr.session_id
            FROM question_results qr
            JOIN questions q ON q.id = qr.question_id
            JOIN chapters ch ON ch.id = qr.chapter_id
            JOIN subjects sub ON sub.id = qr.subject_id
            JOIN test_sessions ts ON ts.id = qr.session_id
            WHERE qr.student_id=? AND qr.classified_mistake_type IS NOT NULL
            ORDER BY ts.submitted_at DESC
        """, (self.student_id,)).fetchall()
        conn.close()

        by_type = defaultdict(list)
        for r in manual_rows:
            by_type[r["error_type"]].append(dict(r))
        for r in live_rows:
            by_type[r["error_type"]].append(dict(r))

        total = len(manual_rows) + len(live_rows)
        return {"by_type": dict(by_type), "total": total}

    def get_test_analytics(self):
        conn = get_db()
        tests = conn.execute("""
            SELECT * FROM mock_tests WHERE student_id=? ORDER BY date
        """, (self.student_id,)).fetchall()
        conn.close()
        result = []
        for t in tests:
            d = dict(t)
            d["percentage"] = round(d["scored_marks"] / d["total_marks"] * 100, 1) if d["total_marks"] else 0
            result.append(d)
        return result

    def get_personalized_recommendations(self):
        weak = self._get_weak_topics(3)
        recs = []
        for topic in weak:
            recs.append({
                "type": "study",
                "chapter": topic["name"],
                "subject": topic["subject"],
                "reason": f"Accuracy is {round(topic['accuracy']*100)}% — needs focused revision",
                "priority": "high" if topic["accuracy"] < 0.5 else "medium"
            })
        return recs


class StudyPlannerService:
    def __init__(self, student_id: int):
        self.student_id = student_id

    def get_plan(self, start_date: str, end_date: str):
        conn = get_db()
        rows = conn.execute("""
            SELECT sp.*, ch.name as chapter_name, s.name as subject_name, s.color
            FROM study_plan sp
            JOIN chapters ch ON ch.id = sp.chapter_id
            JOIN subjects s ON s.id = ch.subject_id
            WHERE sp.student_id=? AND sp.scheduled_date BETWEEN ? AND ?
            ORDER BY sp.scheduled_date
        """, (self.student_id, start_date, end_date)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_plan_item(self, chapter_id: int, date: str, duration: int, plan_type: str = "study"):
        conn = get_db()
        conn.execute("""
            INSERT INTO study_plan (student_id, chapter_id, scheduled_date, duration_mins, plan_type)
            VALUES (?,?,?,?,?)
        """, (self.student_id, chapter_id, date, duration, plan_type))
        conn.commit()
        conn.close()

    def mark_complete(self, plan_id: int):
        conn = get_db()
        conn.execute("UPDATE study_plan SET completed=1 WHERE id=? AND student_id=?",
                     (plan_id, self.student_id))
        conn.commit()
        conn.close()

    # ── Phase 8: Adaptive Study Planner ─────────────────────────────────────
    #
    # generate_daily_plan() reads the Digital Twin + overdue revision queue
    # for this student and writes today's plan to study_plan, replacing any
    # previously AI-generated plan for today (manually-added items are never
    # touched — they're tagged source='manual').
    #
    # Revision items are always first and non-negotiable (spec 8A step 2).
    # Remaining time is distributed across chapters by a priority score that
    # blends mastery gap, syllabus weightage, exam urgency, recent leakage,
    # and avoidance behavior (spec 8A step 4).

    _REVISION_RISK_MINUTES = {"critical": 30, "high": 20, "medium": 15, "low": 15}

    def generate_daily_plan(self, available_hours: float, exam_date: str = None) -> dict:
        from datetime import date, datetime
        from backend.services.digital_twin import DigitalTwinEngine
        from backend.services.revision import RevisionService

        student_id = self.student_id
        conn = get_db()
        try:
            student = conn.execute(
                "SELECT * FROM students WHERE id=?", (student_id,)
            ).fetchone()
            if not student:
                conn.close()
                return {"error": "student not found"}
            student = dict(student)
            exam_target = (student.get("exam_target") or "JEE")

            twin = DigitalTwinEngine(student_id).get()
            topic_mastery = twin.get("topic_mastery", {})
            behavioral_patterns = twin.get("behavioral_patterns", {})
            mistake_distribution = twin.get("mistake_distribution", {})
            avoidance_chapters = set(behavioral_patterns.get("avoidance_chapters", []))

            # ── Step 2: overdue revision items go first, non-negotiable ─────
            revision_items = RevisionService(student_id).get_due_today()
            revision_time_allocated = sum(
                self._REVISION_RISK_MINUTES.get(item["forgetting_risk"], 15)
                for item in revision_items
            )

            # ── Step 3: remaining time for chapter study ─────────────────────
            total_minutes = max(0, round(available_hours * 60))
            remaining_minutes = max(0, total_minutes - revision_time_allocated)

            # ── urgency multiplier from days to exam ─────────────────────────
            effective_exam_date = exam_date or student.get("exam_date")
            days_to_exam = None
            if effective_exam_date:
                try:
                    ed = datetime.strptime(str(effective_exam_date)[:10], "%Y-%m-%d").date()
                    days_to_exam = (ed - date.today()).days
                except ValueError:
                    days_to_exam = None

            if days_to_exam is None or days_to_exam > 60:
                urgency = 1.0
            elif days_to_exam > 30:
                urgency = 1.3
            elif days_to_exam > 15:
                urgency = 1.6
            else:
                urgency = 2.0

            # ── top 3 leakage-source chapters (marks lost on wrong answers) ──
            leakage_rows = conn.execute("""
                SELECT ch.id as chapter_id,
                       SUM(q.marks_correct - qr.marks_awarded) as leaked
                FROM question_results qr
                JOIN questions q ON q.id = qr.question_id
                JOIN chapters ch ON ch.id = qr.chapter_id
                WHERE qr.student_id=? AND qr.is_correct=0 AND qr.is_skipped=0
                GROUP BY ch.id
                HAVING leaked > 0
                ORDER BY leaked DESC LIMIT 3
            """, (student_id,)).fetchall()
            top_leakage_chapter_ids = {r["chapter_id"] for r in leakage_rows}

            # ── all chapters in this student's syllabus ──────────────────────
            chapter_rows = conn.execute("""
                SELECT ch.id, ch.name, ch.weightage, s.name as subject_name
                FROM chapters ch
                JOIN subjects s ON s.id = ch.subject_id
                WHERE lower(s.exam_target) = ?
            """, (exam_target.lower(),)).fetchall()

            scored_chapters = []
            for ch in chapter_rows:
                ch_name = ch["name"]
                mastery_data = topic_mastery.get(ch_name, {})
                # Chapters with no attempts yet have no measured mastery —
                # treat them as 0% so untouched syllabus is never silently
                # skipped in favor of chapters that merely look weak.
                mastery = mastery_data.get("mastery", 0)
                total_attempted = mastery_data.get("total_attempted", 0)

                base = (100 - mastery) / 100 * ch["weightage"]
                in_leakage = ch["id"] in top_leakage_chapter_ids
                in_avoidance = ch_name in avoidance_chapters
                leakage_bonus = 0.3 if in_leakage else 0.0
                avoidance_bonus = 0.2 if in_avoidance else 0.0
                final_score = base * urgency + leakage_bonus + avoidance_bonus

                if final_score <= 0:
                    continue

                scored_chapters.append({
                    "chapter_id": ch["id"],
                    "chapter_name": ch_name,
                    "subject_name": ch["subject_name"],
                    "weightage": ch["weightage"],
                    "mastery": mastery,
                    "total_attempted": total_attempted,
                    "in_leakage": in_leakage,
                    "in_avoidance": in_avoidance,
                    "final_score": final_score,
                })

            scored_chapters.sort(key=lambda x: -x["final_score"])

            # ── Step 5: allocate remaining time, min 20 / max 90 per chapter ─
            allocations = self._allocate_plan_minutes(scored_chapters, remaining_minutes)

            # ── Step 6: build plan items with priority_reason strings ────────
            plan_items = []
            for item in revision_items:
                mins = self._REVISION_RISK_MINUTES.get(item["forgetting_risk"], 15)
                desc = (item.get("description") or "").strip()
                desc_preview = desc[:80] + ("…" if len(desc) > 80 else "")
                plan_items.append({
                    "type": "revision",
                    "chapter_id": item["chapter_id"],
                    "chapter_name": item.get("chapter_name", ""),
                    "subject_name": None,
                    "duration_mins": mins,
                    "priority_reason": (
                        f"Revision due — {item['forgetting_risk']} forgetting risk "
                        f"(rep #{item.get('repetition_count', 0) + 1}). {desc_preview}"
                    ),
                    "revision_item_id": item["id"],
                })

            for ch, mins in allocations:
                reasons = [f"{round(ch['mastery'])}% mastery"]
                if ch["total_attempted"]:
                    mtype_count = mistake_distribution.get("conceptual", 0)
                    reasons.append(
                        f"{ch['total_attempted']} attempts"
                        + (f", {mtype_count} conceptual errors overall" if mtype_count else "")
                    )
                else:
                    reasons.append("no attempts yet")
                reasons.append(f"weightage {ch['weightage']} in {exam_target}")
                if ch["in_leakage"]:
                    reasons.append("top leakage source")
                if ch["in_avoidance"]:
                    reasons.append("avoidance pattern detected")

                plan_items.append({
                    "type": "study",
                    "chapter_id": ch["chapter_id"],
                    "chapter_name": ch["chapter_name"],
                    "subject_name": ch["subject_name"],
                    "duration_mins": mins,
                    "priority_reason": f"{ch['chapter_name']} ({', '.join(reasons)})",
                    "revision_item_id": None,
                })

            # ── Step 7: DELETE today's existing AI-generated plan, INSERT new ─
            today_str = date.today().isoformat()
            conn.execute(
                "DELETE FROM study_plan WHERE student_id=? AND scheduled_date=? AND source='ai_generated'",
                (student_id, today_str),
            )
            for item in plan_items:
                cur = conn.execute("""
                    INSERT INTO study_plan
                        (student_id, chapter_id, scheduled_date, duration_mins,
                         plan_type, priority_reason, source)
                    VALUES (?,?,?,?,?,?,'ai_generated')
                """, (
                    student_id, item["chapter_id"], today_str, item["duration_mins"],
                    item["type"], item["priority_reason"],
                ))
                item["plan_id"] = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        study_minutes = sum(i["duration_mins"] for i in plan_items if i["type"] == "study")
        return {
            "date": today_str,
            "available_hours": available_hours,
            "days_to_exam": days_to_exam,
            "revision_time_allocated": revision_time_allocated,
            "study_time_allocated": study_minutes,
            "total_time_allocated": revision_time_allocated + study_minutes,
            "items": plan_items,
        }

    @staticmethod
    def _allocate_plan_minutes(scored_chapters: list, total_minutes: int,
                                min_mins: int = 20, max_mins: int = 90):
        """
        Proportional time allocation clamped to [min_mins, max_mins].
        Highest-priority chapters are allocated first; each gets a share of
        the original budget proportional to its final_score among chapters
        not yet allocated, clamped, and capped by whatever budget remains.
        Stops once the remaining budget can no longer support the minimum.
        """
        allocations = []
        remaining_budget = total_minutes
        remaining = list(scored_chapters)
        while remaining_budget >= min_mins and remaining:
            total_score = sum(max(c["final_score"], 0.01) for c in remaining)
            ch = remaining[0]
            share = max(ch["final_score"], 0.01) / total_score
            mins = round(total_minutes * share)
            mins = max(min_mins, min(max_mins, mins))
            mins = min(mins, remaining_budget)
            if mins < min_mins:
                break
            allocations.append((ch, mins))
            remaining_budget -= mins
            remaining.pop(0)
        return allocations


class MemoryService:
    def __init__(self, student_id: int):
        self.student_id = student_id

    def get_history(self, limit=20):
        conn = get_db()
        rows = conn.execute("""
            SELECT role, content FROM chat_memory
            WHERE student_id=? ORDER BY created_at DESC LIMIT ?
        """, (self.student_id, limit)).fetchall()
        conn.close()
        return list(reversed([dict(r) for r in rows]))

    def save_message(self, role: str, content: str):
        conn = get_db()
        conn.execute("""
            INSERT INTO chat_memory (student_id, role, content) VALUES (?,?,?)
        """, (self.student_id, role, content))
        # Keep only last 100 messages
        conn.execute("""
            DELETE FROM chat_memory WHERE student_id=? AND id NOT IN (
                SELECT id FROM chat_memory WHERE student_id=? ORDER BY created_at DESC LIMIT 100
            )
        """, (self.student_id, self.student_id))
        conn.commit()
        conn.close()

    def get_student_context(self):
        """Legacy fallback for students with no twin data yet."""
        conn = get_db()
        student = conn.execute("SELECT * FROM students WHERE id=?", (self.student_id,)).fetchone()
        weak = conn.execute("""
            SELECT ch.name, cp.accuracy FROM chapter_performance cp
            JOIN chapters ch ON ch.id=cp.chapter_id
            WHERE cp.student_id=? AND cp.total_attempted>0 ORDER BY cp.accuracy ASC LIMIT 3
        """, (self.student_id,)).fetchall()
        total_hours = conn.execute(
            "SELECT COALESCE(SUM(duration_mins),0)/60.0 FROM study_sessions WHERE student_id=?",
            (self.student_id,)
        ).fetchone()[0]
        conn.close()

        if not student:
            return ""
        weak_str = ", ".join(f"{r[0]} ({round(r[1]*100)}%)" for r in weak) if weak else "none yet"
        return (
            f"Student: {student['name']}, Target Exam: {student['exam_target']}. "
            f"Total study: {round(total_hours,1)}h. Weak topics: {weak_str}."
        )

    def get_mentor_briefing(self, twin: dict, student: dict) -> str:
        """
        Phase 5: Build a fully structured, data-rich mentor briefing from the
        Digital Twin. Every section contains specific numbers — no generic text.
        Depth adapts to unlock_level so the mentor never invents data it doesn't have.
        """
        from datetime import date, datetime

        n = twin.get("tests_analyzed", 0)
        level = twin.get("unlock_level", 1)
        bp = twin.get("behavioral_patterns", {})
        sc = twin.get("scores", {})
        rls = twin.get("rank_leakage_summary", {})
        hist = twin.get("score_history", [])
        pred = twin.get("predictions", {})
        md = twin.get("mistake_distribution", {})
        tm = twin.get("topic_mastery", {})
        exam_target = student.get("exam_target", "JEE")

        lines = []

        # ── STUDENT PROFILE ────────────────────────────────────────────────
        lines.append("━━ STUDENT PROFILE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"Name: {student.get('name', 'Unknown')} | Exam: {exam_target} | Tests Analyzed: {n}")
        lines.append(f"Unlock Level: {level}/4")
        target_rank = student.get("target_rank")
        lines.append(f"Target Rank: {target_rank if target_rank else 'not set'}")

        exam_date_s = student.get("exam_date")
        if exam_date_s:
            try:
                ed = datetime.strptime(exam_date_s[:10], "%Y-%m-%d").date()
                days_left = (ed - date.today()).days
                lines.append(f"Days to Exam: {days_left}")
            except ValueError:
                lines.append("Days to Exam: not set")
        else:
            lines.append("Days to Exam: not set")

        # ── SCORE TRAJECTORY ───────────────────────────────────────────────
        if hist:
            lines.append("\n━━ SCORE TRAJECTORY ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            recent = hist[-3:]
            pcts = " → ".join(f"{h['percentage']}%" for h in recent)
            lines.append(f"Recent {len(recent)} tests: {pcts}")

            # Compute trend from score history
            if len(hist) >= 3:
                old_avg = sum(h["percentage"] for h in hist[-6:-3]) / 3 if len(hist) >= 6 else hist[0]["percentage"]
                new_avg = sum(h["percentage"] for h in hist[-3:]) / 3
                if new_avg > old_avg + 3:
                    trend = "improving"
                elif new_avg < old_avg - 3:
                    trend = "declining"
                else:
                    trend = "stable"
            elif len(hist) == 2:
                trend = "improving" if hist[-1]["percentage"] > hist[0]["percentage"] else "declining"
            else:
                trend = "insufficient data"
            lines.append(f"Trend: {trend}")

            leaked = rls.get("avg_leaked_per_test", 0)
            top_src = rls.get("top_leakage_source", "unknown")
            if leaked:
                lines.append(f"Latest avg leakage: {leaked} marks/test lost ({top_src} errors dominant)")

        # ── BEHAVIORAL FLAGS (Level 2+) ────────────────────────────────────
        if level >= 2:
            lines.append("\n━━ BEHAVIORAL FLAGS (Level 2+) ━━━━━━━━━━━━━━━━")
            panic_frac = bp.get("panic_fraction", 0)
            panic_n = bp.get("panic_detected_count", 0)
            lines.append(f"Panic behavior: detected in {panic_n}/{n} tests — panic fraction: {panic_frac*100:.0f}%")
            penalty_avg = bp.get("second_guess_penalty_avg_marks", 0)
            lines.append(f"Second-guess penalty: avg {penalty_avg:.1f} marks/test lost to answer changes in panic window")
            avoid = bp.get("avoidance_chapters", [])
            if avoid:
                lines.append(f"Avoidance: {', '.join(avoid)} skipped in >30% of tests")
            else:
                lines.append("Avoidance: none detected")
            over_r = bp.get("overconfidence_rate", 0)
            under_r = bp.get("underconfidence_rate", 0)
            lines.append(f"Overconfidence rate: {over_r*100:.0f}% | Underconfidence rate: {under_r*100:.0f}%")

        # ── COGNITIVE PROFILE (Level 3+) ───────────────────────────────────
        if level >= 3:
            lines.append("\n━━ COGNITIVE PROFILE (Level 3+) ━━━━━━━━━━━━━━━")
            score_fields = [
                ("Conceptual Understanding", "conceptual_understanding"),
                ("Problem Solving", "problem_solving"),
                ("Time Management", "time_management"),
                ("Confidence Calibration", "confidence_calibration"),
                ("Retention", "retention"),
                ("Adaptability", "adaptability"),
                ("Exam Readiness", "exam_readiness"),
            ]
            for label, key in score_fields:
                v = sc.get(key)
                lines.append(f"{label}: {f'{v}/100' if v is not None else 'not yet available'}")

            # ── WEAKEST TOPICS (Level 3+) ──────────────────────────────────
            lines.append("\n━━ WEAKEST TOPICS (Level 3+) ━━━━━━━━━━━━━━━━━━")
            weakest = sorted(
                [(ch, d) for ch, d in tm.items() if d.get("total_attempted", 0) > 0],
                key=lambda x: x[1]["mastery"]
            )[:3]
            if weakest:
                for i, (ch, d) in enumerate(weakest, 1):
                    arrow = {"improving": "↑", "declining": "↓", "stable": "→"}.get(d.get("trend", "stable"), "→")
                    lines.append(f"{i}. {ch}: {d['mastery']}% mastery {arrow} | {d.get('total_attempted', 0)} attempts")
            else:
                lines.append("No chapter mastery data yet — take more tests.")

        # ── TOP LEAKAGE SOURCES (Level 2+) ────────────────────────────────
        if level >= 2 and md:
            lines.append("\n━━ TOP LEAKAGE SOURCES (Level 2+) ━━━━━━━━━━━━━")
            sorted_leakage = sorted(md.items(), key=lambda x: -x[1])[:3]
            avg_marks = rls.get("avg_leaked_per_test", 0)
            for mtype, count in sorted_leakage:
                if count > 0:
                    lines.append(f"• {mtype}: {count} total occurrences | avg {avg_marks:.1f} marks/test leaked overall")

        # ── PREDICTIONS (Level 4+) ─────────────────────────────────────────
        if level >= 2 and pred.get("score_range_low") is not None:
            lines.append("\n━━ PREDICTIONS (Level 4+) ━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"Predicted score: {pred['score_range_low']}–{pred['score_range_high']} / {hist[-1].get('max_score', 300) if hist else 300}")
            lines.append(f"Predicted rank: {pred['rank_range_low']:,}–{pred['rank_range_high']:,}")
            lines.append(f"Confidence: {pred.get('confidence_pct')}% (based on {n} tests)")
            if pred.get("on_track") is not None:
                status = "YES ✓" if pred["on_track"] else "NO ✗"
                lines.append(f"On track for target rank {target_rank or 'N/A'}: {status}")

        # ── TODAY'S ACTION ─────────────────────────────────────────────────
        action = twin.get("todays_highest_impact_action", "")
        if action:
            lines.append("\n━━ TODAY'S HIGHEST IMPACT ACTION ━━━━━━━━━━━━━━")
            lines.append(action)

        return "\n".join(lines)
