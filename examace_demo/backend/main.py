from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load API keys from <project root>/.env if present (python-dotenv is optional)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass

from backend.models.database import init_db, get_db
from backend.services.analytics import AnalyticsService, StudyPlannerService, MemoryService
from backend.services.test_session_service import (
    create_session, insert_events, submit_session,
)
from backend.services.pipeline import run_post_submit_pipeline
from backend.services.trends import TrendsService  # Phase 9

app = FastAPI(title="ExamAce API", version="1.0.0")
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "EXAMACE_CORS_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500,null",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

# ── Pydantic Models ──────────────────────────────────────────────────────────

class StudentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    exam_target: Literal["JEE", "NEET", "UPSC"]

class StudentGoalUpdate(BaseModel):
    target_rank: Optional[int] = None
    exam_date: Optional[str] = None
    target_score: Optional[float] = None

class StudySessionCreate(BaseModel):
    student_id: int
    chapter_id: int
    duration_mins: int = Field(gt=0, le=1440)
    date: Optional[str] = None
    notes: str = Field(default="", max_length=2000)

class MockTestCreate(BaseModel):
    student_id: int
    exam_target: Literal["JEE", "NEET", "UPSC"]
    title: str = Field(min_length=1, max_length=200)
    total_marks: int = Field(gt=0)
    scored_marks: float = Field(ge=0)
    time_taken_mins: int = Field(gt=0, le=1440)
    date: Optional[str] = None
    chapter_scores: dict = Field(default_factory=dict)

class MistakeCreate(BaseModel):
    student_id: int
    chapter_id: int
    question_text: str = Field(min_length=1, max_length=4000)
    correct_answer: str = Field(default="", max_length=2000)
    student_answer: str = Field(default="", max_length=2000)
    error_type: Literal["conceptual", "silly", "formula", "calculation", "time"] = "conceptual"

class PlanItemCreate(BaseModel):
    student_id: int
    chapter_id: int
    date: str
    duration_mins: int = Field(gt=0, le=1440)
    plan_type: Literal["study", "revision", "test"] = "study"

class PlanGenerateRequest(BaseModel):
    student_id: int
    available_hours: float = Field(default=6, gt=0, le=16)
    exam_date: Optional[str] = None

class ChatMessage(BaseModel):
    student_id: int
    message: str = Field(min_length=1, max_length=4000)

class QuestionCreate(BaseModel):
    external_id: Optional[str] = None
    exam_target: Literal["JEE", "NEET", "UPSC"]
    subject_id: int
    chapter_id: int
    subtopic_id: Optional[int] = None
    question_type: Literal["single_correct", "multi_correct", "integer", "passage_based"] = "single_correct"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    difficulty_score: float = Field(default=0.5, ge=0.0, le=1.0)
    question_text: str = Field(min_length=1, max_length=8000)
    option_a: Optional[str] = None
    option_b: Optional[str] = None
    option_c: Optional[str] = None
    option_d: Optional[str] = None
    correct_answer: str = Field(min_length=1, max_length=50)
    solution_text: str = Field(default="", max_length=8000)
    marks_correct: float = 4.0
    marks_incorrect: float = -1.0
    marks_partial: float = 2.0
    source: Literal["previous_year", "coaching", "generated"] = "generated"
    year: Optional[int] = None
    population_avg_time_secs: Optional[float] = None
    population_accuracy: Optional[float] = None
    tags: list[str] = Field(default_factory=list)

class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    exam_target: Literal["JEE", "NEET", "UPSC"]
    test_type: Literal["full_mock", "subject_mock", "chapter_mock", "custom"] = "custom"
    duration_mins: int = Field(default=180, gt=0, le=1440)
    total_marks: int = Field(default=300, gt=0)
    marking_scheme: dict = Field(default_factory=lambda: {"correct": 4, "incorrect": -1, "partial": 2})
    instructions: str = Field(default="", max_length=4000)
    is_published: bool = False

class TemplateQuestionAdd(BaseModel):
    question_id: int
    section_name: str = Field(default="General", max_length=100)
    section_order: int = Field(default=1, ge=1)
    question_order: int = Field(ge=1)
    marks_correct: Optional[float] = None
    marks_incorrect: Optional[float] = None

class SessionCreate(BaseModel):
    student_id: int
    template_id: int
    energy_level: int = Field(default=3, ge=1, le=5)

class EventIn(BaseModel):
    event_type: str = Field(min_length=1, max_length=50)
    event_data: dict = Field(default_factory=dict)
    occurred_at: int
    question_id: Optional[int] = None

class EventBatch(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=500)

class SessionSubmit(BaseModel):
    answers: dict = Field(default_factory=dict)  # {question_id(str): answer}
    final_30min_feeling: Literal["normal", "rushed", "panicked"] = "normal"
    energy_level: int = Field(default=3, ge=1, le=5)

DIFFICULTY_TIME_DEFAULTS = {"easy": 60, "medium": 120, "hard": 200}
DIFFICULTY_ACCURACY_DEFAULTS = {"easy": 0.75, "medium": 0.50, "hard": 0.25}

# ── Students ─────────────────────────────────────────────────────────────────

@app.post("/api/students")
def create_student(data: StudentCreate):
    conn = get_db()
    try:
        c = conn.execute(
            "INSERT INTO students (name, email, exam_target) VALUES (?,?,?)",
            (data.name, data.email, data.exam_target.lower())
        )
        conn.commit()
        student_id = c.lastrowid
        conn.close()
        return {"id": student_id, "message": "Student created"}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))

@app.get("/api/students/{sid}")
def get_student(sid: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Student not found")
    return dict(row)

@app.get("/api/students")
def list_students():
    conn = get_db()
    rows = conn.execute("SELECT * FROM students ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.patch("/api/students/{sid}/goals")
def update_student_goals(sid: int, data: StudentGoalUpdate):
    """Update target_rank, exam_date, target_score for a student."""
    conn = get_db()
    updates = {}
    if data.target_rank is not None:
        updates["target_rank"] = data.target_rank
    if data.exam_date is not None:
        updates["exam_date"] = data.exam_date
    if data.target_score is not None:
        updates["target_score"] = data.target_score
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE students SET {set_clause} WHERE id=?",
            list(updates.values()) + [sid],
        )
        conn.commit()
    conn.close()
    return {"message": "Goals updated"}

# ── Digital Twin ──────────────────────────────────────────────────────────────

@app.get("/api/students/{sid}/twin")
def get_twin(sid: int):
    """Return the full Digital Twin JSON for a student."""
    from backend.services.digital_twin import DigitalTwinEngine
    twin = DigitalTwinEngine(sid).get()
    return twin

@app.post("/api/students/{sid}/twin/reset")
def reset_twin(sid: int, x_admin_key: Optional[str] = Header(None)):
    """Admin-only: reset the twin to empty state. Used for testing."""
    import os
    expected = os.getenv("EXAMACE_ADMIN_KEY", "")
    if expected and x_admin_key != expected:
        raise HTTPException(403, "Admin key required")
    conn = get_db()
    conn.execute("UPDATE students SET profile_json='{}' WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {"message": "Twin reset"}

# ── Subjects & Chapters ───────────────────────────────────────────────────────

@app.get("/api/subjects")
def get_subjects(exam_target: Optional[str] = None):
    conn = get_db()
    if exam_target:
        rows = conn.execute("SELECT * FROM subjects WHERE lower(exam_target)=?", (exam_target.lower(),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM subjects").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/chapters")
def get_chapters(subject_id: Optional[int] = None, exam_target: Optional[str] = None):
    conn = get_db()
    if subject_id:
        rows = conn.execute("""
            SELECT ch.*, s.name as subject_name, s.color FROM chapters ch
            JOIN subjects s ON s.id=ch.subject_id WHERE ch.subject_id=?
        """, (subject_id,)).fetchall()
    elif exam_target:
        rows = conn.execute("""
            SELECT ch.*, s.name as subject_name, s.color FROM chapters ch
            JOIN subjects s ON s.id=ch.subject_id WHERE lower(s.exam_target)=?
        """, (exam_target.lower(),)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ch.*, s.name as subject_name, s.color FROM chapters ch
            JOIN subjects s ON s.id=ch.subject_id
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Study Sessions ────────────────────────────────────────────────────────────

@app.post("/api/study-sessions")
def add_study_session(data: StudySessionCreate):
    conn = get_db()
    conn.execute("""
        INSERT INTO study_sessions (student_id, chapter_id, duration_mins, date, notes)
        VALUES (?,?,?,COALESCE(?,date('now')),?)
    """, (data.student_id, data.chapter_id, data.duration_mins, data.date, data.notes))
    conn.commit()
    conn.close()
    return {"message": "Session logged"}

@app.get("/api/study-sessions/{sid}")
def get_study_sessions(sid: int, limit: int = 20):
    conn = get_db()
    rows = conn.execute("""
        SELECT ss.*, ch.name as chapter_name, s.name as subject_name
        FROM study_sessions ss
        JOIN chapters ch ON ch.id=ss.chapter_id
        JOIN subjects s ON s.id=ch.subject_id
        WHERE ss.student_id=? ORDER BY ss.date DESC LIMIT ?
    """, (sid, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Mock Tests ────────────────────────────────────────────────────────────────

@app.post("/api/mock-tests")
def add_mock_test(data: MockTestCreate):
    conn = get_db()
    conn.execute("""
        INSERT INTO mock_tests (student_id, exam_target, title, total_marks, scored_marks, time_taken_mins, date)
        VALUES (?,?,?,?,?,?,COALESCE(?,date('now')))
    """, (data.student_id, data.exam_target.lower(), data.title, data.total_marks,
          data.scored_marks, data.time_taken_mins, data.date))
    # Update chapter performance from scores
    for ch_id_str, scores in (data.chapter_scores or {}).items():
        ch_id = int(ch_id_str)
        correct = scores.get("correct", 0)
        total = scores.get("total", 1)
        if total <= 0 or correct < 0 or correct > total:
            raise HTTPException(400, "Invalid chapter score totals")
        acc = correct / total
        conn.execute("""
            INSERT INTO chapter_performance (student_id, chapter_id, accuracy, total_attempted, total_correct)
            VALUES (?,?,?,?,?)
            ON CONFLICT(student_id, chapter_id) DO UPDATE SET
                accuracy=(total_correct+excluded.total_correct)*1.0/(total_attempted+excluded.total_attempted),
                total_attempted=total_attempted+excluded.total_attempted,
                total_correct=total_correct+excluded.total_correct,
                last_updated=datetime('now')
        """, (data.student_id, ch_id, acc, total, correct))
    conn.commit()
    conn.close()
    return {"message": "Test recorded"}

@app.get("/api/mock-tests/{sid}")
def get_mock_tests(sid: int):
    svc = AnalyticsService(sid)
    return svc.get_test_analytics()

# ── Mistakes ──────────────────────────────────────────────────────────────────

@app.post("/api/mistakes")
def add_mistake(data: MistakeCreate):
    conn = get_db()
    conn.execute("""
        INSERT INTO mistakes (student_id, chapter_id, question_text, correct_answer, student_answer, error_type)
        VALUES (?,?,?,?,?,?)
    """, (data.student_id, data.chapter_id, data.question_text,
          data.correct_answer, data.student_answer, data.error_type))
    conn.commit()
    conn.close()
    return {"message": "Mistake logged"}

@app.patch("/api/mistakes/{mid}/resolve")
def resolve_mistake(mid: int):
    conn = get_db()
    cur = conn.execute("UPDATE mistakes SET resolved=1 WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Mistake not found")
    return {"message": "Resolved"}

@app.get("/api/mistakes/{sid}")
def get_mistakes(sid: int):
    svc = AnalyticsService(sid)
    return svc.get_mistake_analysis()

# ── Questions & Test Templates ───────────────────────────────────────────────

def _check_admin_key(x_admin_key: Optional[str]):
    expected = os.getenv("EXAMACE_ADMIN_KEY")
    if not expected:
        raise HTTPException(503, "Admin routes are not configured. Set EXAMACE_ADMIN_KEY on the backend.")
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(401, "Invalid or missing X-Admin-Key header")

@app.get("/api/questions")
def get_questions(
    exam_target: Optional[str] = None,
    chapter_id: Optional[int] = None,
    difficulty: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 50,
):
    limit = max(1, min(limit, 200))
    conn = get_db()
    clauses = ["q.is_active=1"]
    params = []
    if exam_target:
        clauses.append("lower(q.exam_target)=?")
        params.append(exam_target.lower())
    if chapter_id:
        clauses.append("q.chapter_id=?")
        params.append(chapter_id)
    if difficulty:
        clauses.append("q.difficulty=?")
        params.append(difficulty)
    if type:
        clauses.append("q.question_type=?")
        params.append(type)
    where = " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT q.*, ch.name as chapter_name, s.name as subject_name, st.name as subtopic_name
        FROM questions q
        JOIN chapters ch ON ch.id = q.chapter_id
        JOIN subjects s ON s.id = q.subject_id
        LEFT JOIN subtopics st ON st.id = q.subtopic_id
        WHERE {where}
        ORDER BY q.id LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/questions/{qid}")
def get_question(qid: int):
    conn = get_db()
    row = conn.execute("""
        SELECT q.*, ch.name as chapter_name, s.name as subject_name, st.name as subtopic_name
        FROM questions q
        JOIN chapters ch ON ch.id = q.chapter_id
        JOIN subjects s ON s.id = q.subject_id
        LEFT JOIN subtopics st ON st.id = q.subtopic_id
        WHERE q.id=?
    """, (qid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Question not found")
    return dict(row)

@app.post("/api/admin/questions")
def create_question(data: QuestionCreate, x_admin_key: Optional[str] = Header(default=None)):
    _check_admin_key(x_admin_key)
    avg_time = data.population_avg_time_secs
    if avg_time is None:
        avg_time = DIFFICULTY_TIME_DEFAULTS[data.difficulty]
    accuracy = data.population_accuracy
    if accuracy is None:
        accuracy = DIFFICULTY_ACCURACY_DEFAULTS[data.difficulty]

    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO questions (
                external_id, exam_target, subject_id, chapter_id, subtopic_id,
                question_type, difficulty, difficulty_score, question_text,
                option_a, option_b, option_c, option_d, correct_answer, solution_text,
                marks_correct, marks_incorrect, marks_partial, source, year,
                population_avg_time_secs, population_accuracy, population_sample_size,
                tags, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,1)
        """, (
            data.external_id, data.exam_target, data.subject_id, data.chapter_id, data.subtopic_id,
            data.question_type, data.difficulty, data.difficulty_score, data.question_text,
            data.option_a, data.option_b, data.option_c, data.option_d, data.correct_answer, data.solution_text,
            data.marks_correct, data.marks_incorrect, data.marks_partial, data.source, data.year,
            avg_time, accuracy, json.dumps(data.tags),
        ))
        conn.commit()
        qid = cur.lastrowid
        conn.close()
        return {"id": qid, "message": "Question created"}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))

@app.get("/api/templates")
def get_templates(exam_target: Optional[str] = None):
    conn = get_db()
    if exam_target:
        rows = conn.execute("""
            SELECT t.*, COUNT(tq.id) as question_count
            FROM test_templates t
            LEFT JOIN template_questions tq ON tq.template_id = t.id
            WHERE lower(t.exam_target)=? AND t.is_published=1
            GROUP BY t.id ORDER BY t.created_at DESC
        """, (exam_target.lower(),)).fetchall()
    else:
        rows = conn.execute("""
            SELECT t.*, COUNT(tq.id) as question_count
            FROM test_templates t
            LEFT JOIN template_questions tq ON tq.template_id = t.id
            WHERE t.is_published=1
            GROUP BY t.id ORDER BY t.created_at DESC
        """).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["marking_scheme"] = json.loads(d["marking_scheme"] or "{}")
        results.append(d)
    return results

@app.get("/api/templates/{tid}")
def get_template(tid: int):
    conn = get_db()
    template = conn.execute("SELECT * FROM test_templates WHERE id=?", (tid,)).fetchone()
    if not template:
        conn.close()
        raise HTTPException(404, "Template not found")
    questions = conn.execute("""
        SELECT tq.section_name, tq.section_order, tq.question_order,
               COALESCE(tq.marks_correct, q.marks_correct) as marks_correct,
               COALESCE(tq.marks_incorrect, q.marks_incorrect) as marks_incorrect,
               q.*
        FROM template_questions tq
        JOIN questions q ON q.id = tq.question_id
        WHERE tq.template_id=?
        ORDER BY tq.section_order, tq.question_order
    """, (tid,)).fetchall()
    conn.close()
    result = dict(template)
    result["marking_scheme"] = json.loads(result["marking_scheme"] or "{}")
    result["questions"] = [dict(q) for q in questions]
    return result

@app.post("/api/admin/templates")
def create_template(data: TemplateCreate, x_admin_key: Optional[str] = Header(default=None)):
    _check_admin_key(x_admin_key)
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO test_templates (name, exam_target, test_type, duration_mins, total_marks,
            marking_scheme, instructions, is_published, created_by)
        VALUES (?,?,?,?,?,?,?,?,NULL)
    """, (
        data.name, data.exam_target.lower(), data.test_type, data.duration_mins, data.total_marks,
        json.dumps(data.marking_scheme), data.instructions, int(data.is_published),
    ))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return {"id": tid, "message": "Template created"}

@app.post("/api/admin/templates/{tid}/questions")
def add_template_question(tid: int, data: TemplateQuestionAdd, x_admin_key: Optional[str] = Header(default=None)):
    _check_admin_key(x_admin_key)
    conn = get_db()
    template = conn.execute("SELECT id FROM test_templates WHERE id=?", (tid,)).fetchone()
    if not template:
        conn.close()
        raise HTTPException(404, "Template not found")
    question = conn.execute("SELECT id FROM questions WHERE id=?", (data.question_id,)).fetchone()
    if not question:
        conn.close()
        raise HTTPException(404, "Question not found")
    try:
        conn.execute("""
            INSERT INTO template_questions
                (template_id, question_id, section_name, section_order, question_order,
                 marks_correct, marks_incorrect)
            VALUES (?,?,?,?,?,?,?)
        """, (
            tid, data.question_id, data.section_name, data.section_order, data.question_order,
            data.marks_correct, data.marks_incorrect,
        ))
        conn.commit()
        conn.close()
        return {"message": "Question added to template"}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))

# ── Live Test Sessions ───────────────────────────────────────────────────────

@app.post("/api/sessions")
def start_session(data: SessionCreate, background_tasks: BackgroundTasks):
    student = get_db().execute("SELECT id FROM students WHERE id=?", (data.student_id,)).fetchone()
    if not student:
        raise HTTPException(404, "Student not found")

    session_id, template, questions = create_session(data.student_id, data.template_id, data.energy_level)
    if session_id is None:
        raise HTTPException(404, "Template not found")
    if not questions:
        raise HTTPException(400, "This template has no questions yet")

    # Strip correct_answer / solution_text before sending to the client —
    # the live test interface must never receive the answer key up front.
    safe_questions = []
    for q in questions:
        safe = {k: v for k, v in q.items() if k not in ("correct_answer", "solution_text")}
        safe_questions.append(safe)

    return {
        "session_id": session_id,
        "template": {
            "id": template["id"],
            "name": template["name"],
            "duration_mins": template["duration_mins"],
            "total_marks": template["total_marks"],
            "marking_scheme": json.loads(template["marking_scheme"] or "{}"),
            "instructions": template["instructions"],
        },
        "questions": safe_questions,
    }

@app.post("/api/sessions/{sid}/events")
def post_session_events(sid: int, data: EventBatch):
    conn = get_db()
    session = conn.execute("SELECT student_id, status FROM test_sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    if not session:
        raise HTTPException(404, "Session not found")
    received = insert_events(sid, session["student_id"], data.events)
    return {"received": received}

@app.post("/api/sessions/{sid}/submit")
def submit_session_route(sid: int, data: SessionSubmit, background_tasks: BackgroundTasks):
    conn = get_db()
    session = conn.execute("SELECT student_id, status FROM test_sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    if not session:
        raise HTTPException(404, "Session not found")

    result = submit_session(
        sid, session["student_id"], data.answers, data.final_30min_feeling, data.energy_level
    )
    if result is None:
        raise HTTPException(404, "Session not found")
    if not result.get("already_submitted"):
        background_tasks.add_task(run_post_submit_pipeline, sid, session["student_id"])
    return result

@app.get("/api/sessions/{sid}/result")
def get_session_result(sid: int):
    conn = get_db()
    session = conn.execute("""
        SELECT s.*, t.name as template_name
        FROM test_sessions s
        LEFT JOIN test_templates t ON t.id = s.template_id
        WHERE s.id=?
    """, (sid,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Session not found")
    results = conn.execute("""
        SELECT qr.*, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
               q.solution_text, q.question_type, q.difficulty, ch.name as chapter_name
        FROM question_results qr
        JOIN questions q ON q.id = qr.question_id
        JOIN chapters ch ON ch.id = qr.chapter_id
        WHERE qr.session_id=?
        ORDER BY qr.id
    """, (sid,)).fetchall()
    conn.close()
    session_dict = dict(session)
    session_dict["behavioral_flags_json"] = json.loads(session_dict["behavioral_flags_json"] or "{}")
    session_dict["leakage_report_json"] = json.loads(session_dict["leakage_report_json"] or "{}")
    return {
        "session": session_dict,
        "template_name": session_dict.get("template_name", ""),
        "question_results": [dict(r) for r in results],
        "analysis_ready": bool(session["analysis_ready"]),
    }

@app.get("/api/sessions/student/{student_id}")
def get_student_sessions(student_id: int):
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id, s.template_id, t.name as template_name, s.status,
               s.started_at, s.submitted_at, s.duration_secs,
               s.raw_score, s.max_score, s.analysis_ready
        FROM test_sessions s
        JOIN test_templates t ON t.id = s.template_id
        WHERE s.student_id=?
        ORDER BY s.started_at DESC
    """, (student_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/sessions/{sid}/analysis")
def get_session_analysis(sid: int):
    conn = get_db()
    session = conn.execute(
        "SELECT analysis_ready, behavioral_flags_json, leakage_report_json FROM test_sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    if not session:
        raise HTTPException(404, "Session not found")
    if not session["analysis_ready"]:
        return {"analysis_ready": False, "behavioral_flags": None, "leakage_report": None}
    return {
        "analysis_ready": True,
        "behavioral_flags": json.loads(session["behavioral_flags_json"] or "{}"),
        "leakage_report": json.loads(session["leakage_report_json"] or "{}"),
    }

# ── Analytics & Dashboard ────────────────────────────────────────────────────

@app.get("/api/dashboard/{sid}")
def get_dashboard(sid: int):
    svc = AnalyticsService(sid)
    return svc.get_dashboard_stats()

@app.get("/api/performance/{sid}")
def get_performance(sid: int):
    svc = AnalyticsService(sid)
    return svc.get_chapter_performance()

@app.get("/api/recommendations/{sid}")
def get_recommendations(sid: int):
    svc = AnalyticsService(sid)
    return svc.get_personalized_recommendations()

@app.get("/api/students/{sid}/trends")
def get_trends(sid: int):
    """
    Phase 9B — Trends page. Aggregates every analyzed test into score
    trend, leakage trend, mistake-type evolution, chapter mastery
    evolution, and behavioral improvement trend. Read-only, no writes.
    """
    return TrendsService(sid).get_trends()

# ── Study Planner ─────────────────────────────────────────────────────────────

@app.post("/api/plan")
def add_plan(data: PlanItemCreate):
    svc = StudyPlannerService(data.student_id)
    svc.add_plan_item(data.chapter_id, data.date, data.duration_mins, data.plan_type)
    return {"message": "Plan item added"}

@app.get("/api/plan/{sid}")
def get_plan(sid: int, start: str = "2024-01-01", end: str = "2030-12-31"):
    svc = StudyPlannerService(sid)
    return svc.get_plan(start, end)

@app.patch("/api/plan/{pid}/complete")
def complete_plan(pid: int, student_id: int):
    svc = StudyPlannerService(student_id)
    svc.mark_complete(pid)
    return {"message": "Marked complete"}

@app.post("/api/plan/generate")
def generate_plan(data: PlanGenerateRequest):
    """
    Phase 8 — Adaptive Study Planner.
    Reads the Digital Twin + overdue revision queue and generates today's
    plan: revision items first (non-negotiable), then chapters ranked by
    mastery gap, weightage, exam urgency, leakage, and avoidance behavior.
    Writes to study_plan (source='ai_generated') and returns the plan.
    """
    svc = StudyPlannerService(data.student_id)
    result = svc.generate_daily_plan(data.available_hours, data.exam_date)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

# ── AI Mentor Chat ────────────────────────────────────────────────────────────

class _RateLimited(Exception):
    pass

def _llm_provider():
    """LLM_PROVIDER=groq|anthropic forces a choice; otherwise Groq if its key exists, else Anthropic."""
    forced = (os.getenv("LLM_PROVIDER") or "").lower().strip()
    if forced == "groq" and os.getenv("GROQ_API_KEY"):
        return "groq"
    if forced == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None

async def _call_llm(provider: str, system_prompt: str, messages: list) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        if provider == "groq":
            model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "max_tokens": 1200,
                "temperature": 0.4,
            }
            if model.startswith("openai/gpt-oss"):
                payload["reasoning_effort"] = "low"   # keep reasoning tokens (and quota use) small
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 429:
                raise _RateLimited()
            if resp.status_code >= 400:
                raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:300]}")
            return resp.json()["choices"][0]["message"]["content"] or ""

        # anthropic
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json",
                     "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                     "anthropic-version": "2023-06-01"},
            json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                  "max_tokens": 1200, "system": system_prompt, "messages": messages},
        )
        if resp.status_code == 429:
            raise _RateLimited()
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


@app.post("/api/chat")
async def chat(data: ChatMessage):
    """Proxy to Claude API with long-term memory and Phase 5 full mentor briefing."""
    provider = _llm_provider()
    if not provider:
        return {"reply": "AI Mentor is not configured yet. Set GROQ_API_KEY (free) or ANTHROPIC_API_KEY on the backend to enable chat."}

    mem = MemoryService(data.student_id)
    # Free-tier friendly: fewer history turns, each capped in length (token budget)
    history = mem.get_history(int(os.getenv("MENTOR_HISTORY", "8")))

    # Phase 5: Always load twin + student, then call get_mentor_briefing()
    # which builds a structured, data-rich briefing adapted to unlock_level.
    from backend.services.digital_twin import DigitalTwinEngine
    conn_s = get_db()
    student = conn_s.execute("SELECT * FROM students WHERE id=?", (data.student_id,)).fetchone()
    conn_s.close()
    student_dict = dict(student) if student else {"name": "Student", "exam_target": "JEE"}

    twin = DigitalTwinEngine(data.student_id).get()
    tests_analyzed = twin.get("tests_analyzed", 0)
    unlock_level = twin.get("unlock_level", 1)

    if tests_analyzed > 0:
        # Phase 5: Full structured briefing from twin data
        briefing = mem.get_mentor_briefing(twin, student_dict)
        system_prompt = f"""You are the ExamAce AI Mentor.
You are not a generic tutor. You are a long-term performance coach who tracks this student's behavioral patterns across every test they take.

You know exactly why this student is losing marks. You have data. You speak from that data. You never speak generically.

Your job is to answer these five questions for this student:
1. Why is this student losing marks?
2. Why are they not improving?
3. What is preventing them from reaching their target rank?
4. What should they do next?
5. What is the highest-impact action they can take today?

Always end every response with one specific prescription for tomorrow. Not a suggestion. A prescription with a time estimate and what exactly to do.

━━━ STUDENT DATA ━━━
{briefing}
━━━━━━━━━━━━━━━━━━━

ABSOLUTE RULES:
- Never give advice that ignores the data above
- Never say "study more" without naming the specific chapter and why
- Always reference specific numbers from the data ("your panic fraction is 60%" not "you sometimes panic")
- If unlock_level < 2: acknowledge limited data, encourage taking the next test
- If tests_analyzed < 3: tell the student you are still building their profile
- Never invent data. If a metric is null or not in the briefing: say it is not available yet
- If panic_fraction > 0.4: address panic behavior in every response about scores — do not skip it
- If student asks about a topic listed in their avoidance chapters: note the avoidance pattern explicitly before answering
- If student seems discouraged: reference their improving trend data (if it exists) before anything else
- If unlock_level == 1: end every response with "Complete 2 more tests to unlock behavioral coaching."
"""
    else:
        # True first-timer: no twin data at all — warm onboarding context
        context = mem.get_student_context()
        system_prompt = f"""You are ExamAce AI Mentor — an expert tutor for IIT-JEE, NEET, and UPSC preparation.
You are warm, encouraging, and highly knowledgeable. You explain concepts with clarity, use analogies, and help students overcome weak areas.

Student Context: {context}

Guidelines:
- Be concise but thorough
- Use examples and step-by-step explanations when explaining concepts
- Encourage the student warmly
- If they ask about weak topics, give focused help
- For problems, show a complete solution with steps
- Encourage them to take their first live test to unlock personalized behavioral coaching
- End every response with: "Take your first live test to unlock personalized coaching based on your actual exam behavior."
"""

    messages = []
    for m in history:
        messages.append({"role": m["role"], "content": (m["content"] or "")[:1500]})
    messages.append({"role": "user", "content": data.message})

    try:
        reply = await _call_llm(provider, system_prompt, messages)
    except _RateLimited:
        return {"reply": "The AI Mentor has hit its free usage limit for now. Please try again in a few minutes."}
    except Exception as e:
        import logging
        logging.error(f"Chat API error ({provider}): {e}")
        return {"reply": "I'm having trouble connecting right now. Please try again in a moment."}

    # Save the exchange only after success, so a failed call never leaves a dangling user turn
    mem.save_message("user", data.message)
    mem.save_message("assistant", reply)
    return {"reply": reply}

@app.get("/api/chat/history/{sid}")
def get_chat_history(sid: int):
    mem = MemoryService(sid)
    return mem.get_history(50)

# ── Serve Frontend ────────────────────────────────────────────────────────────
frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.get("/")
def root():
    return {"status": "ExamAce API running", "docs": "/docs"}

# ── Phase 6 — Rank Intelligence ───────────────────────────────────────────────

@app.get("/api/students/{sid}/rank-intel")
def get_rank_intel(sid: int):
    """
    Aggregate endpoint for the Rank Intelligence Dashboard.
    Returns twin data + last session leakage + score history in one call.
    """
    from backend.services.digital_twin import DigitalTwinEngine
    twin = DigitalTwinEngine(sid).get()

    conn = get_db()
    # Last 8 submitted sessions with score + template name
    sessions = conn.execute("""
        SELECT s.id, s.raw_score, s.max_score, s.submitted_at,
               s.leakage_report_json, s.behavioral_flags_json, s.analysis_ready,
               t.name as template_name
        FROM test_sessions s
        JOIN test_templates t ON t.id = s.template_id
        WHERE s.student_id=? AND s.status='submitted'
        ORDER BY s.submitted_at DESC
        LIMIT 8
    """, (sid,)).fetchall()

    # Aggregate leakage across all sessions
    leakage_totals: dict = {}
    leakage_counts: dict = {}
    for row in sessions:
        try:
            lr = json.loads(row["leakage_report_json"] or "{}")
            breakdown = lr.get("breakdown", {})
            for t_type, item in breakdown.items():
                marks = float(item.get("marks", 0))
                cnt = int(item.get("count", 0))
                leakage_totals[t_type] = leakage_totals.get(t_type, 0) + marks
                leakage_counts[t_type] = leakage_counts.get(t_type, 0) + cnt
        except Exception:
            pass

    n_sessions = len(sessions)
    leakage_by_type = []
    for mt, total_marks in sorted(leakage_totals.items(), key=lambda x: -x[1]):
        avg_marks = total_marks / n_sessions if n_sessions else 0
        leakage_by_type.append({
            "mistake_type": mt,
            "total_marks_lost": round(total_marks, 1),
            "avg_marks_per_test": round(avg_marks, 2),
            "count": leakage_counts.get(mt, 0),
        })

    # Latest session flags
    latest_flags = {}
    if sessions:
        try:
            latest_flags = json.loads(sessions[0]["behavioral_flags_json"] or "{}")
        except Exception:
            pass

    # Score history (chronological, last 8)
    score_history = [
        {
            "id": r["id"],
            "template_name": r["template_name"],
            "raw_score": r["raw_score"],
            "max_score": r["max_score"],
            "pct": round(r["raw_score"] / r["max_score"] * 100, 1) if r["max_score"] else 0,
            "submitted_at": r["submitted_at"],
        }
        for r in reversed(sessions)  # oldest first for chart
    ]

    conn.close()

    return {
        "twin": twin,
        "leakage_by_type": leakage_by_type,
        "latest_flags": latest_flags,
        "score_history": score_history,
        "sessions_count": n_sessions,
    }


# ── Phase 7: Adaptive Spaced Repetition ──────────────────────────────────────

class ReviewRequest(BaseModel):
    quality: Literal["got_it", "with_hint", "forgot"]


@app.get("/api/revision/{sid}")
def get_revision_due(sid: int):
    """Items due today + overdue, ordered by forgetting risk (critical first)."""
    from backend.services.revision import RevisionService
    return RevisionService(sid).get_due_today()


@app.get("/api/revision/{sid}/all")
def get_revision_all(sid: int):
    """All revision queue items for the student."""
    from backend.services.revision import RevisionService
    return RevisionService(sid).get_all()


@app.post("/api/revision/{item_id}/review")
def review_revision(item_id: int, data: ReviewRequest, student_id: int):
    """Apply SM-2 review. quality: got_it | with_hint | forgot"""
    from backend.services.revision import RevisionService
    result = RevisionService(student_id).review_item(item_id, data.quality)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.delete("/api/revision/{item_id}")
def delete_revision(item_id: int, student_id: int):
    from backend.services.revision import RevisionService
    deleted = RevisionService(student_id).delete_item(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"deleted": True}
