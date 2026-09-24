import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "examace.db"

def get_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        exam_target TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        profile_json TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        exam_target TEXT NOT NULL,
        color TEXT DEFAULT '#6366f1'
    );

    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER REFERENCES subjects(id),
        name TEXT NOT NULL,
        weightage REAL DEFAULT 1.0
    );

    CREATE TABLE IF NOT EXISTS study_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        chapter_id INTEGER REFERENCES chapters(id),
        duration_mins INTEGER DEFAULT 0,
        date TEXT DEFAULT (date('now')),
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS mock_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        exam_target TEXT NOT NULL,
        title TEXT NOT NULL,
        total_marks INTEGER DEFAULT 0,
        scored_marks REAL DEFAULT 0,
        time_taken_mins INTEGER DEFAULT 0,
        date TEXT DEFAULT (date('now')),
        questions_json TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS mistakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        chapter_id INTEGER REFERENCES chapters(id),
        question_text TEXT NOT NULL,
        correct_answer TEXT,
        student_answer TEXT,
        error_type TEXT DEFAULT 'conceptual',
        resolved INTEGER DEFAULT 0,
        date TEXT DEFAULT (date('now'))
    );

    CREATE TABLE IF NOT EXISTS study_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        chapter_id INTEGER REFERENCES chapters(id),
        scheduled_date TEXT NOT NULL,
        duration_mins INTEGER DEFAULT 60,
        completed INTEGER DEFAULT 0,
        plan_type TEXT DEFAULT 'study'
    );

    CREATE TABLE IF NOT EXISTS chat_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS chapter_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        chapter_id INTEGER REFERENCES chapters(id),
        accuracy REAL DEFAULT 0,
        total_attempted INTEGER DEFAULT 0,
        total_correct INTEGER DEFAULT 0,
        last_updated TEXT DEFAULT (datetime('now')),
        UNIQUE(student_id, chapter_id)
    );

    CREATE TABLE IF NOT EXISTS subtopics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        UNIQUE(chapter_id, name)
    );

    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id TEXT,
        exam_target TEXT NOT NULL,
        subject_id INTEGER REFERENCES subjects(id),
        chapter_id INTEGER REFERENCES chapters(id),
        subtopic_id INTEGER REFERENCES subtopics(id),
        question_type TEXT NOT NULL DEFAULT 'single_correct',
        difficulty TEXT NOT NULL DEFAULT 'medium',
        difficulty_score REAL DEFAULT 0.5,
        question_text TEXT NOT NULL,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct_answer TEXT NOT NULL,
        solution_text TEXT DEFAULT '',
        marks_correct REAL DEFAULT 4.0,
        marks_incorrect REAL DEFAULT -1.0,
        marks_partial REAL DEFAULT 2.0,
        source TEXT DEFAULT 'generated',
        year INTEGER,
        population_avg_time_secs REAL DEFAULT 120,
        population_accuracy REAL DEFAULT 0.5,
        population_sample_size INTEGER DEFAULT 0,
        tags TEXT DEFAULT '[]',
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS test_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        exam_target TEXT NOT NULL,
        test_type TEXT NOT NULL DEFAULT 'custom',
        duration_mins INTEGER DEFAULT 180,
        total_marks INTEGER DEFAULT 300,
        marking_scheme TEXT DEFAULT '{"correct":4,"incorrect":-1,"partial":2}',
        instructions TEXT DEFAULT '',
        is_published INTEGER DEFAULT 0,
        created_by INTEGER REFERENCES students(id),
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS template_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER REFERENCES test_templates(id) ON DELETE CASCADE,
        question_id INTEGER REFERENCES questions(id),
        section_name TEXT DEFAULT 'General',
        section_order INTEGER DEFAULT 1,
        question_order INTEGER NOT NULL,
        marks_correct REAL,
        marks_incorrect REAL,
        UNIQUE(template_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS test_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id),
        template_id INTEGER REFERENCES test_templates(id),
        started_at TEXT DEFAULT (datetime('now')),
        submitted_at TEXT,
        duration_secs INTEGER,
        status TEXT DEFAULT 'in_progress'
            CHECK(status IN ('in_progress','submitted','abandoned','timed_out')),
        raw_score REAL DEFAULT 0,
        max_score REAL DEFAULT 0,
        percentile REAL,
        rank_in_session INTEGER,
        final_30min_feeling TEXT DEFAULT 'normal'
            CHECK(final_30min_feeling IN ('normal','rushed','panicked')),
        energy_level INTEGER DEFAULT 3
            CHECK(energy_level BETWEEN 1 AND 5),
        behavioral_flags_json TEXT DEFAULT '{}',
        leakage_report_json TEXT DEFAULT '{}',
        analysis_ready INTEGER DEFAULT 0,
        client_info TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS test_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER REFERENCES test_sessions(id) ON DELETE CASCADE,
        student_id INTEGER REFERENCES students(id),
        question_id INTEGER REFERENCES questions(id),
        event_type TEXT NOT NULL,
        event_data TEXT DEFAULT '{}',
        occurred_at INTEGER NOT NULL,
        server_received_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS question_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER REFERENCES test_sessions(id) ON DELETE CASCADE,
        student_id INTEGER REFERENCES students(id),
        question_id INTEGER REFERENCES questions(id),
        chapter_id INTEGER REFERENCES chapters(id),
        subject_id INTEGER REFERENCES subjects(id),
        selected_answer TEXT,
        correct_answer TEXT,
        is_correct INTEGER DEFAULT 0,
        is_skipped INTEGER DEFAULT 0,
        was_revisited INTEGER DEFAULT 0,
        revisit_count INTEGER DEFAULT 0,
        answer_change_count INTEGER DEFAULT 0,
        answer_change_log TEXT DEFAULT '[]',
        time_spent_ms INTEGER DEFAULT 0,
        time_to_first_answer_ms INTEGER DEFAULT 0,
        marks_awarded REAL DEFAULT 0,
        confidence_rating TEXT,
        population_avg_time_secs INTEGER,
        population_accuracy REAL,
        time_ratio REAL,
        classified_mistake_type TEXT,
        mistake_confidence REAL DEFAULT 0,
        in_panic_window INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_students_email ON students(email);
    CREATE INDEX IF NOT EXISTS idx_subjects_exam_target ON subjects(exam_target);
    CREATE INDEX IF NOT EXISTS idx_chapters_subject_id ON chapters(subject_id);
    CREATE INDEX IF NOT EXISTS idx_study_sessions_student_date ON study_sessions(student_id, date);
    CREATE INDEX IF NOT EXISTS idx_mock_tests_student_date ON mock_tests(student_id, date);
    CREATE INDEX IF NOT EXISTS idx_mistakes_student_date ON mistakes(student_id, date);
    CREATE INDEX IF NOT EXISTS idx_study_plan_student_date ON study_plan(student_id, scheduled_date);
    CREATE INDEX IF NOT EXISTS idx_chat_memory_student_created ON chat_memory(student_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_questions_chapter_id ON questions(chapter_id);
    CREATE INDEX IF NOT EXISTS idx_questions_exam_target ON questions(exam_target);
    CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
    CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions(question_type);
    CREATE INDEX IF NOT EXISTS idx_questions_subtopic_id ON questions(subtopic_id);
    CREATE INDEX IF NOT EXISTS idx_template_questions_template_id ON template_questions(template_id);
    CREATE INDEX IF NOT EXISTS idx_test_sessions_student_id ON test_sessions(student_id);
    CREATE INDEX IF NOT EXISTS idx_test_sessions_template_id ON test_sessions(template_id);
    CREATE INDEX IF NOT EXISTS idx_test_sessions_submitted_at ON test_sessions(submitted_at DESC);
    CREATE INDEX IF NOT EXISTS idx_test_events_session_id ON test_events(session_id);
    CREATE INDEX IF NOT EXISTS idx_test_events_session_question ON test_events(session_id, question_id);
    CREATE INDEX IF NOT EXISTS idx_test_events_event_type ON test_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_question_results_session_id ON question_results(session_id);
    CREATE INDEX IF NOT EXISTS idx_question_results_student_chapter ON question_results(student_id, chapter_id);
    CREATE INDEX IF NOT EXISTS idx_question_results_mistake_type ON question_results(classified_mistake_type);

    CREATE TABLE IF NOT EXISTS revision_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
        chapter_id INTEGER REFERENCES chapters(id),
        question_id INTEGER REFERENCES questions(id),
        item_type TEXT NOT NULL DEFAULT 'question'
            CHECK(item_type IN ('question','concept','formula')),
        description TEXT NOT NULL,
        due_date TEXT NOT NULL,
        interval_days INTEGER DEFAULT 1,
        ease_factor REAL DEFAULT 2.5,
        repetition_count INTEGER DEFAULT 0,
        last_reviewed_at TEXT,
        forgetting_risk TEXT DEFAULT 'high'
            CHECK(forgetting_risk IN ('critical','high','medium','low')),
        source_session_id INTEGER REFERENCES test_sessions(id),
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_revision_queue_student_due
        ON revision_queue(student_id, due_date);
    CREATE INDEX IF NOT EXISTS idx_revision_queue_student_risk
        ON revision_queue(student_id, forgetting_risk);
    """)

    # Phase 4: add student goal columns (safe no-ops if columns already exist)
    for col_def in [
        "ALTER TABLE students ADD COLUMN target_rank INTEGER",
        "ALTER TABLE students ADD COLUMN exam_date TEXT",
        "ALTER TABLE students ADD COLUMN target_score REAL",
        "ALTER TABLE test_sessions ADD COLUMN benchmarks_applied INTEGER DEFAULT 0",
        # Phase 7: "correct but ambiguous" detection — flags answers that were
        # right but show signs of guessing / lucky elimination / no solid
        # conceptual basis, so the student doesn't mistake luck for mastery.
        "ALTER TABLE question_results ADD COLUMN is_ambiguous_correct INTEGER DEFAULT 0",
        "ALTER TABLE question_results ADD COLUMN ambiguous_reason TEXT",
        # Phase 8: Adaptive Study Planner — AI-generated plan items carry a
        # human-readable reason string and a 'source' tag so generated items
        # (source='ai_generated') can be distinguished from and safely
        # replaced/regenerated without touching manually-added items.
        "ALTER TABLE study_plan ADD COLUMN priority_reason TEXT DEFAULT ''",
        "ALTER TABLE study_plan ADD COLUMN source TEXT DEFAULT 'manual'",
    ]:
        try:
            c.execute(col_def)
        except Exception:
            pass  # column already exists

    # Normalise exam_target casing across all tables (safe idempotent update)
    for table in ("test_templates", "questions", "students", "mock_tests"):
        try:
            c.execute(f"UPDATE {table} SET exam_target = LOWER(exam_target) WHERE exam_target != LOWER(exam_target)")
        except Exception:
            pass  # table or column doesn't exist in this DB version

    # Seed / migrate subjects and chapters (safe upsert — never loses existing data)
    _migrate_syllabus(c)
    conn.commit()

    # Seed question bank + test templates (internally guarded; replaces old low-quality seed)
    from backend.services.question_seeder import seed_questions
    seed_questions(conn)

    # Generate the full 22 mock test templates via mock_test_generator
    # (idempotent — skips templates that already exist by name)
    try:
        from backend.services.mock_test_generator import generate_mock_tests
        generate_mock_tests(conn)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"mock_test_generator skipped: {e}")

    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE NATIONAL SYLLABUS
# weightage values are based on historical JEE / NEET question frequency analysis
# ─────────────────────────────────────────────────────────────────────────────

NATIONAL_SYLLABUS = {
    # ── JEE PHYSICS ──────────────────────────────────────────────────────────
    ("Physics", "JEE", "#3b82f6"): [
        ("Units & Measurements",           1.5),
        ("Kinematics",                     4.5),
        ("Laws of Motion",                 5.0),
        ("Work, Energy & Power",           4.5),
        ("Rotational Motion",              6.0),
        ("Gravitation",                    3.5),
        ("Properties of Matter",           3.0),
        ("Thermal Properties of Matter",   3.5),
        ("Thermodynamics",                 5.0),
        ("Kinetic Theory of Gases",        3.0),
        ("Oscillations",                   4.0),
        ("Waves & Sound",                  4.0),
        ("Electrostatics",                 7.0),
        ("Current Electricity",            6.5),
        ("Magnetism & Moving Charges",     5.5),
        ("Electromagnetic Induction",      5.0),
        ("Alternating Current",            4.0),
        ("Electromagnetic Waves",          1.5),
        ("Ray Optics",                     5.0),
        ("Wave Optics",                    4.0),
        ("Dual Nature of Radiation",       3.0),
        ("Atoms & Nuclei",                 4.0),
        ("Semiconductor Electronics",      3.0),
    ],
    # ── JEE CHEMISTRY ────────────────────────────────────────────────────────
    ("Chemistry", "JEE", "#10b981"): [
        ("Some Basic Concepts of Chemistry", 2.0),
        ("Atomic Structure",                 4.0),
        ("Classification of Elements",       3.0),
        ("Chemical Bonding",                 5.5),
        ("States of Matter",                 3.0),
        ("Thermodynamics",                   4.5),
        ("Equilibrium",                      5.0),
        ("Redox Reactions",                  2.5),
        ("Hydrogen",                         1.5),
        ("s-Block Elements",                 2.5),
        ("p-Block Elements I",               4.0),
        ("p-Block Elements II",              4.5),
        ("d and f Block Elements",           4.0),
        ("Coordination Compounds",           5.0),
        ("Haloalkanes & Haloarenes",         3.5),
        ("Alcohols, Phenols & Ethers",       3.5),
        ("Aldehydes, Ketones & Acids",       5.0),
        ("Amines",                           3.5),
        ("Biomolecules",                     2.5),
        ("Polymers",                         2.0),
        ("Solid State",                      3.5),
        ("Solutions",                        4.0),
        ("Electrochemistry",                 4.5),
        ("Chemical Kinetics",               4.5),
        ("Surface Chemistry",               2.0),
        ("Environmental Chemistry",         1.5),
    ],
    # ── JEE MATHEMATICS ──────────────────────────────────────────────────────
    ("Mathematics", "JEE", "#f59e0b"): [
        ("Sets, Relations & Functions",    4.0),
        ("Complex Numbers",                4.5),
        ("Quadratic Equations",            3.5),
        ("Progressions & Series",          4.5),
        ("Permutation & Combination",      3.5),
        ("Binomial Theorem",               3.0),
        ("Matrices & Determinants",        5.0),
        ("Straight Lines",                 4.0),
        ("Circles",                        4.5),
        ("Conic Sections",                 5.5),
        ("Limits & Continuity",            4.0),
        ("Differentiation",                4.5),
        ("Applications of Derivatives",    5.5),
        ("Integration",                    7.0),
        ("Differential Equations",         3.5),
        ("Vectors",                        4.0),
        ("3D Geometry",                    4.5),
        ("Probability",                    4.5),
        ("Statistics",                     2.5),
        ("Trigonometry",                   4.5),
    ],
    # ── NEET PHYSICS ─────────────────────────────────────────────────────────
    ("Physics", "NEET", "#06b6d4"): [
        ("Units, Dimensions & Measurement", 2.0),
        ("Motion in a Straight Line",       3.5),
        ("Motion in a Plane",               3.5),
        ("Laws of Motion",                  5.0),
        ("Work, Energy & Power",            4.5),
        ("System of Particles & Rotational Motion", 5.0),
        ("Gravitation",                     4.0),
        ("Mechanical Properties of Solids", 2.5),
        ("Mechanical Properties of Fluids", 4.0),
        ("Thermal Properties of Matter",    4.0),
        ("Thermodynamics",                  5.0),
        ("Kinetic Theory",                  3.5),
        ("Oscillations",                    4.5),
        ("Waves",                           4.5),
        ("Electric Charges & Fields",       4.0),
        ("Electrostatic Potential & Capacitance", 4.0),
        ("Current Electricity",             5.5),
        ("Moving Charges & Magnetism",      4.5),
        ("Magnetism & Matter",              2.5),
        ("Electromagnetic Induction",       4.0),
        ("Alternating Current",             4.0),
        ("Electromagnetic Waves",           2.0),
        ("Ray Optics & Optical Instruments",5.0),
        ("Wave Optics",                     4.0),
        ("Dual Nature of Radiation & Matter",3.5),
        ("Atoms",                           3.5),
        ("Nuclei",                          4.0),
        ("Semiconductor Electronics",       4.5),
    ],
    # ── NEET CHEMISTRY ───────────────────────────────────────────────────────
    ("Chemistry", "NEET", "#34d399"): [
        ("Some Basic Concepts of Chemistry", 3.0),
        ("Atomic Structure",                 4.5),
        ("Classification of Elements",       3.5),
        ("Chemical Bonding & Molecular Structure", 5.5),
        ("States of Matter",                 3.5),
        ("Thermodynamics",                   4.5),
        ("Equilibrium",                      5.5),
        ("Redox Reactions",                  3.0),
        ("Hydrogen",                         2.0),
        ("s-Block Elements",                 3.0),
        ("p-Block Elements",                 5.5),
        ("d and f Block Elements",           4.0),
        ("Coordination Compounds",           4.5),
        ("Haloalkanes & Haloarenes",         3.5),
        ("Alcohols, Phenols & Ethers",       3.5),
        ("Aldehydes, Ketones & Acids",       5.0),
        ("Amines",                           3.5),
        ("Biomolecules",                     4.5),
        ("Polymers",                         2.5),
        ("Chemistry in Everyday Life",       2.5),
        ("Solid State",                      3.0),
        ("Solutions",                        4.0),
        ("Electrochemistry",                 4.5),
        ("Chemical Kinetics",               4.5),
        ("Surface Chemistry",               2.5),
        ("Environmental Chemistry",         2.0),
    ],
    # ── NEET BIOLOGY — BOTANY ────────────────────────────────────────────────
    ("Biology", "NEET", "#ec4899"): [
        # Class 11 Botany
        ("The Living World",                2.0),
        ("Biological Classification",       3.5),
        ("Plant Kingdom",                   4.5),
        ("Morphology of Flowering Plants",  5.0),
        ("Anatomy of Flowering Plants",     4.5),
        ("Cell: The Unit of Life",          5.5),
        ("Cell Cycle & Cell Division",      5.0),
        ("Transport in Plants",             4.0),
        ("Mineral Nutrition",               4.0),
        ("Photosynthesis in Higher Plants", 6.0),
        ("Respiration in Plants",           4.5),
        ("Plant Growth & Development",      4.0),
        # Class 12 Botany
        ("Sexual Reproduction in Flowering Plants", 5.5),
        ("Principles of Inheritance & Variation",   7.0),
        ("Molecular Basis of Inheritance",          6.5),
        ("Evolution",                               4.5),
        ("Microbes in Human Welfare",               3.5),
        ("Biotechnology — Principles & Processes",  5.0),
        ("Biotechnology & Its Applications",        4.5),
        ("Organisms & Populations",                 4.0),
        ("Ecosystem",                               4.5),
        ("Biodiversity & Conservation",             3.5),
        ("Environmental Issues",                    3.0),
        # Class 11 Zoology
        ("Animal Kingdom",                          5.0),
        ("Structural Organisation in Animals",      4.0),
        ("Biomolecules",                            5.5),
        ("Digestion & Absorption",                  5.5),
        ("Breathing & Exchange of Gases",           5.0),
        ("Body Fluids & Circulation",               5.5),
        ("Excretory Products & Their Elimination",  5.0),
        ("Locomotion & Movement",                   4.5),
        ("Neural Control & Coordination",           5.5),
        ("Chemical Coordination & Integration",     5.0),
        # Class 12 Zoology
        ("Human Reproduction",                      5.5),
        ("Reproductive Health",                     3.5),
        ("Human Health & Disease",                  6.0),
        ("Strategies for Enhancement in Food Production", 3.5),
    ],
    # ── UPSC (retained from original) ────────────────────────────────────────
    ("General Studies", "UPSC", "#8b5cf6"): [
        ("History",           5.0),
        ("Geography",         5.0),
        ("Polity",            5.0),
        ("Economy",           5.0),
        ("Science & Technology", 4.0),
        ("Environment",       4.0),
        ("Current Affairs",   5.0),
        ("Ethics",            4.0),
    ],
}


def _migrate_syllabus(c):
    """
    Safe upsert migration:
    - For each subject in NATIONAL_SYLLABUS, insert if it doesn't exist (matched by name+exam_target).
    - For each chapter, insert if it doesn't exist (matched by subject_id+name).
    - Updates weightage on existing chapters.
    - Never deletes existing subjects, chapters, or questions.
    """
    for (sub_name, exam_target, color), chapters in NATIONAL_SYLLABUS.items():
        # Check if subject exists
        row = c.execute(
            "SELECT id FROM subjects WHERE name=? AND exam_target=?",
            (sub_name, exam_target)
        ).fetchone()
        if row:
            subject_id = row["id"]
        else:
            c.execute(
                "INSERT INTO subjects (name, exam_target, color) VALUES (?,?,?)",
                (sub_name, exam_target, color)
            )
            subject_id = c.lastrowid

        for ch_name, weightage in chapters:
            # Check if chapter exists
            ch_row = c.execute(
                "SELECT id FROM chapters WHERE subject_id=? AND name=?",
                (subject_id, ch_name)
            ).fetchone()
            if ch_row:
                # Update weightage in case it changed
                c.execute(
                    "UPDATE chapters SET weightage=? WHERE id=?",
                    (weightage, ch_row["id"])
                )
            else:
                c.execute(
                    "INSERT INTO chapters (subject_id, name, weightage) VALUES (?,?,?)",
                    (subject_id, ch_name, weightage)
                )
