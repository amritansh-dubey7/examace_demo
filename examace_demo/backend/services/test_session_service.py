"""
Phase 2 service layer for live test sessions.

Scope (Phase 2):
  - Create a test_session from a template and return ordered questions
  - Bulk-accept event batches into test_events
  - Score a submission: correctness + marks_awarded per question_type
  - Write question_results rows with the RAW/objective fields only
    (selected_answer, correct_answer, is_correct, is_skipped, marks_awarded,
    revisit/answer-change counts and time_spent_ms straight from the event
    log — all of these are objective facts, not inferred classifications)
  - Update test_sessions with raw_score/max_score and mark status=submitted

Phase 3 note: run_post_submit_pipeline (the real 15-step behavioral
analysis pipeline) now lives in backend/services/pipeline.py, built on top
of backend/services/behavioral.py's EventAnalyzer / MistakeClassifier /
RankLeakageEngine / PopulationBenchmarkUpdater. This file no longer defines
it — main.py imports it directly from pipeline.py.
"""

import json
from collections import defaultdict
from backend.models.database import get_db


def create_session(student_id: int, template_id: int, energy_level: int):
    conn = get_db()
    template = conn.execute("SELECT * FROM test_templates WHERE id=?", (template_id,)).fetchone()
    if not template:
        conn.close()
        return None, None, None

    questions = conn.execute("""
        SELECT tq.section_name, tq.section_order, tq.question_order,
               COALESCE(tq.marks_correct, q.marks_correct) as marks_correct,
               COALESCE(tq.marks_incorrect, q.marks_incorrect) as marks_incorrect,
               q.*
        FROM template_questions tq
        JOIN questions q ON q.id = tq.question_id
        WHERE tq.template_id=?
        ORDER BY tq.section_order, tq.question_order
    """, (template_id,)).fetchall()

    cur = conn.execute("""
        INSERT INTO test_sessions (student_id, template_id, status, energy_level, max_score)
        VALUES (?,?,?,?,?)
    """, (student_id, template_id, "in_progress", energy_level, template["total_marks"]))
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id, dict(template), [dict(q) for q in questions]


def insert_events(session_id: int, student_id: int, events: list):
    """Bulk insert a batch of client events using executemany. Must be fast.
    
    occurred_at must be an integer epoch-ms timestamp (Date.now() from the
    frontend). Pydantic enforces int on the API boundary, but we add a
    defensive int() cast here as a belt-and-suspenders guard — the behavioral
    engine does arithmetic on this value and will crash if it receives a string.
    """
    if not events:
        return 0
    conn = get_db()
    rows = [
        (
            session_id,
            student_id,
            e.question_id,
            e.event_type,
            json.dumps(e.event_data),
            int(e.occurred_at),   # defensive cast: always store as integer
        )
        for e in events
    ]
    conn.executemany("""
        INSERT INTO test_events (session_id, student_id, question_id, event_type, event_data, occurred_at)
        VALUES (?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def _normalize_answer(answer):
    """multi_correct answers are stored sorted+concatenated, e.g. 'AC'. Selected
    answers from the client may arrive as a list (['A','C']) or string."""
    if answer is None:
        return ""
    if isinstance(answer, list):
        return "".join(sorted(a.strip().upper() for a in answer if a))
    return str(answer).strip().upper()


def _score_question(question_type, selected, correct, marks_correct, marks_incorrect, marks_partial):
    """
    Returns (is_correct, marks_awarded).
    single_correct / integer: exact match -> marks_correct, wrong -> marks_incorrect.
    multi_correct: full match -> marks_correct; partial (subset, no wrong options
    selected) -> marks_partial; any wrong option selected -> marks_incorrect.
    """
    sel = _normalize_answer(selected)
    corr = _normalize_answer(correct)

    if not sel:
        return False, 0.0

    if question_type == "multi_correct":
        sel_set, corr_set = set(sel), set(corr)
        if sel_set == corr_set:
            return True, marks_correct
        if sel_set and sel_set.issubset(corr_set):
            return False, marks_partial
        return False, marks_incorrect

    # single_correct, integer, passage_based (all exact-match types)
    if sel == corr:
        return True, marks_correct
    return False, marks_incorrect


def _aggregate_event_facts(session_id: int):
    """
    Pulls objective per-question facts straight out of the event log:
      time_spent_ms        sum of QuestionLeft.time_spent_ms per question
      revisit_count        count of QuestionRevisited events per question
      answer_change_count  count of AnswerChanged events per question
      answer_change_log    list of {old_answer, new_answer, occurred_at}
      time_to_first_answer_ms
                            occurred_at of first AnswerSelected minus the
                            occurred_at of the first QuestionViewed for that
                            question (objective, no inference needed)
      confidence_rating    last ConfidenceRated value per question
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT question_id, event_type, event_data, occurred_at
        FROM test_events
        WHERE session_id=? AND question_id IS NOT NULL
        ORDER BY occurred_at ASC
    """, (session_id,)).fetchall()
    conn.close()

    time_spent = defaultdict(int)
    revisit_count = defaultdict(int)
    change_count = defaultdict(int)
    change_log = defaultdict(list)
    confidence = {}
    first_viewed_at = {}
    first_answer_at = {}

    for r in rows:
        qid = r["question_id"]
        etype = r["event_type"]
        data = json.loads(r["event_data"] or "{}")
        occurred_at = r["occurred_at"]

        if etype == "QuestionViewed" and qid not in first_viewed_at:
            first_viewed_at[qid] = occurred_at
        elif etype == "QuestionRevisited":
            revisit_count[qid] += 1
        elif etype == "QuestionLeft":
            time_spent[qid] += int(data.get("time_spent_ms", 0) or 0)
        elif etype == "AnswerSelected" and qid not in first_answer_at:
            first_answer_at[qid] = occurred_at
        elif etype == "AnswerChanged":
            change_count[qid] += 1
            change_log[qid].append({
                "old_answer": data.get("old_answer"),
                "new_answer": data.get("new_answer"),
                "occurred_at": occurred_at,
            })
        elif etype == "ConfidenceRated":
            confidence[qid] = data.get("rating")

    time_to_first = {}
    for qid, answered_at in first_answer_at.items():
        viewed_at = first_viewed_at.get(qid)
        if viewed_at is not None:
            time_to_first[qid] = max(0, answered_at - viewed_at)

    return {
        "time_spent_ms": dict(time_spent),
        "revisit_count": dict(revisit_count),
        "answer_change_count": dict(change_count),
        "answer_change_log": dict(change_log),
        "confidence_rating": confidence,
        "time_to_first_answer_ms": time_to_first,
    }


def submit_session(session_id: int, student_id: int, answers: dict, final_30min_feeling: str, energy_level: int):
    """
    Scores the submission and writes question_results rows with objective
    fields only. Returns (score, max_score, percentage) or None if the
    session doesn't exist / doesn't belong to this student / already submitted.
    """
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM test_sessions WHERE id=? AND student_id=?", (session_id, student_id)
    ).fetchone()
    if not session:
        conn.close()
        return None
    if session["status"] != "in_progress":
        conn.close()
        return {
            "score": session["raw_score"], "max_score": session["max_score"],
            "percentage": round(session["raw_score"] / session["max_score"] * 100, 1) if session["max_score"] else 0,
            "session_id": session_id, "already_submitted": True,
        }

    questions = conn.execute("""
        SELECT tq.question_id, COALESCE(tq.marks_correct, q.marks_correct) as marks_correct,
               COALESCE(tq.marks_incorrect, q.marks_incorrect) as marks_incorrect,
               q.marks_partial, q.question_type, q.correct_answer,
               q.chapter_id, q.subject_id,
               q.population_avg_time_secs, q.population_accuracy
        FROM template_questions tq
        JOIN questions q ON q.id = tq.question_id
        WHERE tq.template_id=?
    """, (session["template_id"],)).fetchall()

    facts = _aggregate_event_facts(session_id)
    started_at = session["started_at"]

    total_score = 0.0
    result_rows = []
    for q in questions:
        qid = q["question_id"]
        selected = answers.get(str(qid))
        is_skipped = selected is None or selected == "" or selected == []
        is_correct, marks_awarded = (False, 0.0) if is_skipped else _score_question(
            q["question_type"], selected, q["correct_answer"],
            q["marks_correct"], q["marks_incorrect"], q["marks_partial"],
        )
        total_score += marks_awarded

        change_count = facts["answer_change_count"].get(qid, 0)
        result_rows.append((
            session_id, student_id, qid, q["chapter_id"], q["subject_id"],
            _normalize_answer(selected) if not is_skipped else None,
            q["correct_answer"],
            int(is_correct),
            int(is_skipped),
            int(facts["revisit_count"].get(qid, 0) > 0),
            facts["revisit_count"].get(qid, 0),
            change_count,
            json.dumps(facts["answer_change_log"].get(qid, [])),
            facts["time_spent_ms"].get(qid, 0),
            facts["time_to_first_answer_ms"].get(qid, 0),
            marks_awarded,
            facts["confidence_rating"].get(qid),
            q["population_avg_time_secs"],
            q["population_accuracy"],
        ))

    conn.executemany("""
        INSERT INTO question_results (
            session_id, student_id, question_id, chapter_id, subject_id,
            selected_answer, correct_answer, is_correct, is_skipped,
            was_revisited, revisit_count, answer_change_count, answer_change_log,
            time_spent_ms, time_to_first_answer_ms, marks_awarded,
            confidence_rating, population_avg_time_secs, population_accuracy
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, result_rows)

    duration_secs = None
    started_row = conn.execute("SELECT (julianday('now') - julianday(?)) * 86400 as secs", (started_at,)).fetchone()
    if started_row and started_row["secs"] is not None:
        duration_secs = max(0, int(started_row["secs"]))

    conn.execute("""
        UPDATE test_sessions
        SET status='submitted', submitted_at=datetime('now'), duration_secs=?,
            raw_score=?, final_30min_feeling=?, energy_level=?
        WHERE id=?
    """, (duration_secs, total_score, final_30min_feeling, energy_level, session_id))
    conn.commit()

    max_score = session["max_score"] or 1
    conn.close()
    return {
        "score": round(total_score, 2),
        "max_score": max_score,
        "percentage": round(total_score / max_score * 100, 1) if max_score else 0,
        "session_id": session_id,
    }
