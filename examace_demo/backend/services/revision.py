"""
Phase 7 — Adaptive Spaced Repetition (SM-2 Algorithm)
======================================================
RevisionService handles:
  - auto_create_from_session()  : populate queue from classified mistakes
  - review_item()               : SM-2 update (got_it / with_hint / forgot)
  - get_due_today()             : ordered by forgetting_risk (critical first)
  - get_all()                   : full queue for a student
  - delete_item()               : remove an item

Quality mapping for SM-2:
  got_it    → quality = 5  (perfect recall)
  with_hint → quality = 3  (correct but needed hint)
  forgot    → quality = 1  (complete blackout → reset)

SM-2 update rules (original Wozniak algorithm):
  if quality < 3:
      repetition_count = 0
      interval = 1
  else:
      if repetition_count == 0: interval = 1
      elif repetition_count == 1: interval = 6
      else: interval = round(prev_interval * ease_factor)
      repetition_count += 1
  ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
  ease_factor = max(1.3, ease_factor)

Forgetting risk thresholds:
  interval > 14 days → 'low'
  interval > 5 days  → 'medium'
  interval > 1 day   → 'high'
  interval = 1 day   → 'critical'
"""

import logging
from datetime import date, timedelta
from typing import Literal

from backend.models.database import get_db

logger = logging.getLogger("examace.revision")

QualityLabel = Literal["got_it", "with_hint", "forgot"]

# SM-2 quality scores
_QUALITY_MAP = {
    "got_it": 5,
    "with_hint": 3,
    "forgot": 1,
}


def _interval_to_risk(interval_days: int) -> str:
    if interval_days > 14:
        return "low"
    if interval_days > 5:
        return "medium"
    if interval_days > 1:
        return "high"
    return "critical"


class RevisionService:
    def __init__(self, student_id: int):
        self.student_id = student_id

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO-POPULATE FROM CLASSIFIED MISTAKES
    # ─────────────────────────────────────────────────────────────────────────

    def auto_create_from_session(self, session_id: int) -> int:
        """
        Read question_results for session. For mistakes and avoidances:
          - conceptual / formula_recall → due tomorrow, risk='high'
          - avoidance                   → due today (critical), risk='critical'
          - was_revisited & correct     → due in 3 days, risk='medium'
        Deduplication: if (student_id, question_id) already in queue,
          keep the earlier due_date and reset interval to 1.
        Returns count of items created/updated.
        """
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT qr.id, qr.question_id, qr.chapter_id,
                       qr.classified_mistake_type, qr.is_correct,
                       qr.is_skipped, qr.was_revisited,
                       q.question_text, c.name AS chapter_name
                FROM question_results qr
                JOIN questions q ON q.id = qr.question_id
                JOIN chapters c ON c.id = qr.chapter_id
                WHERE qr.session_id = ?
                  AND qr.student_id = ?
                """,
                (session_id, self.student_id),
            ).fetchall()

            today_str = date.today().isoformat()
            tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
            in_3_days_str = (date.today() + timedelta(days=3)).isoformat()

            count = 0
            for row in rows:
                mt = row["classified_mistake_type"]
                is_correct = bool(row["is_correct"])
                was_revisited = bool(row["was_revisited"])
                q_id = row["question_id"]
                ch_id = row["chapter_id"]
                q_text = row["question_text"] or ""
                description = (q_text[:200] + "...") if len(q_text) > 200 else q_text

                due_date = None
                risk = "high"

                if mt in ("conceptual", "formula_recall"):
                    # Question-level item: student attempted but got it wrong conceptually
                    due_date = tomorrow_str
                    risk = "high"

                    # Deduplication by (student_id, question_id)
                    existing = conn.execute(
                        """
                        SELECT id, due_date FROM revision_queue
                        WHERE student_id = ? AND question_id = ?
                        """,
                        (self.student_id, q_id),
                    ).fetchone()

                    if existing:
                        earlier = min(existing["due_date"], due_date)
                        conn.execute(
                            """
                            UPDATE revision_queue
                            SET due_date=?, interval_days=1, forgetting_risk=?,
                                source_session_id=?
                            WHERE id=?
                            """,
                            (earlier, risk, session_id, existing["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO revision_queue
                              (student_id, chapter_id, question_id, item_type,
                               description, due_date, interval_days, ease_factor,
                               repetition_count, forgetting_risk, source_session_id,
                               created_at)
                            VALUES (?,?,?,?,?,?,1,2.5,0,?,?,datetime('now'))
                            """,
                            (
                                self.student_id, ch_id, q_id,
                                "question", description,
                                due_date, risk, session_id,
                            ),
                        )
                        count += 1

                elif mt == "avoidance":
                    # Chapter-level CONCEPT item — student skipped without trying.
                    # question_id is NULL: this is about the chapter, not one question.
                    due_date = today_str
                    risk = "critical"
                    ch_description = (
                        f"Review {row['chapter_name']} — avoidance pattern detected "
                        f"(questions skipped without attempting in this test)"
                    )

                    # Deduplication by (student_id, chapter_id) for concept items
                    existing = conn.execute(
                        """
                        SELECT id, due_date FROM revision_queue
                        WHERE student_id = ? AND chapter_id = ?
                          AND item_type = 'concept' AND question_id IS NULL
                        """,
                        (self.student_id, ch_id),
                    ).fetchone()

                    if existing:
                        earlier = min(existing["due_date"], due_date)
                        conn.execute(
                            """
                            UPDATE revision_queue
                            SET due_date=?, interval_days=1, forgetting_risk=?,
                                source_session_id=?
                            WHERE id=?
                            """,
                            (earlier, risk, session_id, existing["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO revision_queue
                              (student_id, chapter_id, question_id, item_type,
                               description, due_date, interval_days, ease_factor,
                               repetition_count, forgetting_risk, source_session_id,
                               created_at)
                            VALUES (?,?,NULL,?,?,?,1,2.5,0,?,?,datetime('now'))
                            """,
                            (
                                self.student_id, ch_id,
                                "concept", ch_description,
                                due_date, risk, session_id,
                            ),
                        )
                        count += 1

                elif was_revisited and is_correct:
                    # Knew it but hesitated — question-level, review in 3 days
                    due_date = in_3_days_str
                    risk = "medium"

                    existing = conn.execute(
                        """
                        SELECT id, due_date FROM revision_queue
                        WHERE student_id = ? AND question_id = ?
                        """,
                        (self.student_id, q_id),
                    ).fetchone()

                    if existing:
                        earlier = min(existing["due_date"], due_date)
                        conn.execute(
                            """
                            UPDATE revision_queue
                            SET due_date=?, interval_days=1, forgetting_risk=?,
                                source_session_id=?
                            WHERE id=?
                            """,
                            (earlier, risk, session_id, existing["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO revision_queue
                              (student_id, chapter_id, question_id, item_type,
                               description, due_date, interval_days, ease_factor,
                               repetition_count, forgetting_risk, source_session_id,
                               created_at)
                            VALUES (?,?,?,?,?,?,1,2.5,0,?,?,datetime('now'))
                            """,
                            (
                                self.student_id, ch_id, q_id,
                                "question", description,
                                due_date, risk, session_id,
                            ),
                        )
                        count += 1

                elif not is_correct and mt is not None:
                    # Other wrong answers (guessing, careless, time_pressure etc.)
                    due_date = tomorrow_str
                    risk = "high"

                    existing = conn.execute(
                        """
                        SELECT id, due_date FROM revision_queue
                        WHERE student_id = ? AND question_id = ?
                        """,
                        (self.student_id, q_id),
                    ).fetchone()

                    if existing:
                        earlier = min(existing["due_date"], due_date)
                        conn.execute(
                            """
                            UPDATE revision_queue
                            SET due_date=?, interval_days=1, forgetting_risk=?,
                                source_session_id=?
                            WHERE id=?
                            """,
                            (earlier, risk, session_id, existing["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO revision_queue
                              (student_id, chapter_id, question_id, item_type,
                               description, due_date, interval_days, ease_factor,
                               repetition_count, forgetting_risk, source_session_id,
                               created_at)
                            VALUES (?,?,?,?,?,?,1,2.5,0,?,?,datetime('now'))
                            """,
                            (
                                self.student_id, ch_id, q_id,
                                "question", description,
                                due_date, risk, session_id,
                            ),
                        )
                        count += 1

            conn.commit()
            logger.info(
                "revision auto_create session=%s student=%s items=%s",
                session_id, self.student_id, count,
            )
            return count
        except Exception:
            logger.exception("revision auto_create failed session=%s", session_id)
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # SM-2 REVIEW
    # ─────────────────────────────────────────────────────────────────────────

    def review_item(self, item_id: int, quality_label: QualityLabel) -> dict:
        """
        Apply SM-2 update and return updated item state.
        """
        conn = get_db()
        try:
            item = conn.execute(
                "SELECT * FROM revision_queue WHERE id=? AND student_id=?",
                (item_id, self.student_id),
            ).fetchone()
            if not item:
                return {"error": "item not found"}

            quality = _QUALITY_MAP[quality_label]
            n = item["repetition_count"]
            ef = item["ease_factor"]
            prev_interval = item["interval_days"]

            # SM-2 algorithm
            if quality < 3:
                new_n = 0
                new_interval = 1
            else:
                if n == 0:
                    new_interval = 1
                elif n == 1:
                    new_interval = 6
                else:
                    new_interval = round(prev_interval * ef)
                new_n = n + 1

            # Update ease factor
            new_ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            new_ef = max(1.3, round(new_ef, 4))

            new_due = (date.today() + timedelta(days=new_interval)).isoformat()
            new_risk = _interval_to_risk(new_interval)

            conn.execute(
                """
                UPDATE revision_queue SET
                    interval_days=?, ease_factor=?, repetition_count=?,
                    due_date=?, forgetting_risk=?, last_reviewed_at=datetime('now')
                WHERE id=?
                """,
                (new_interval, new_ef, new_n, new_due, new_risk, item_id),
            )
            conn.commit()

            logger.info(
                "SM-2 item=%s quality=%s(%s) n=%s→%s ef=%.2f→%.2f interval=%s→%s risk=%s",
                item_id, quality_label, quality,
                n, new_n, ef, new_ef, prev_interval, new_interval, new_risk,
            )

            return {
                "item_id": item_id,
                "quality": quality_label,
                "new_interval_days": new_interval,
                "new_ease_factor": new_ef,
                "new_repetition_count": new_n,
                "new_due_date": new_due,
                "forgetting_risk": new_risk,
            }
        except Exception:
            logger.exception("SM-2 update failed item=%s", item_id)
            conn.rollback()
            return {"error": "update failed"}
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # QUERIES
    # ─────────────────────────────────────────────────────────────────────────

    def get_due_today(self) -> list[dict]:
        """
        Items where due_date <= today, ordered critical→high→medium→low,
        then by due_date (oldest overdue first).
        """
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT rq.*, c.name AS chapter_name,
                       q.question_text AS full_question_text
                FROM revision_queue rq
                JOIN chapters c ON c.id = rq.chapter_id
                LEFT JOIN questions q ON q.id = rq.question_id
                WHERE rq.student_id = ?
                  AND rq.due_date <= date('now')
                ORDER BY
                  CASE rq.forgetting_risk
                    WHEN 'critical' THEN 1
                    WHEN 'high'     THEN 2
                    WHEN 'medium'   THEN 3
                    WHEN 'low'      THEN 4
                    ELSE 5
                  END,
                  rq.due_date ASC
                """,
                (self.student_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all(self) -> list[dict]:
        """All revision queue items for the student."""
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT rq.*, c.name AS chapter_name,
                       q.question_text AS full_question_text
                FROM revision_queue rq
                JOIN chapters c ON c.id = rq.chapter_id
                LEFT JOIN questions q ON q.id = rq.question_id
                WHERE rq.student_id = ?
                ORDER BY
                  CASE rq.forgetting_risk
                    WHEN 'critical' THEN 1
                    WHEN 'high'     THEN 2
                    WHEN 'medium'   THEN 3
                    WHEN 'low'      THEN 4
                    ELSE 5
                  END,
                  rq.due_date ASC
                """,
                (self.student_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_item(self, item_id: int) -> bool:
        conn = get_db()
        try:
            c = conn.execute(
                "DELETE FROM revision_queue WHERE id=? AND student_id=?",
                (item_id, self.student_id),
            )
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()
