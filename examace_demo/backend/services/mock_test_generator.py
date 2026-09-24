"""
Mock Test Generator — Production Grade
=======================================
Generates and seeds 22 Mock Test Templates (JEE Main, JEE Advanced, NEET)
using the newly seeded high-quality questions (where fingerprint IS NOT NULL).

Guarantees:
1. No question duplicates within the same test.
2. Minimum repetition of questions across all 22 mock tests (uses least-used questions first).
3. Strict separation: JEE Main tests only use JEE Main questions, JEE Advanced only use JEE Advanced, NEET only use NEET.
4. Correct marking schemes and durations per exam target and test type.
"""

import sqlite3
import json
import random
import logging
from backend.models.database import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Blueprints for the 22 mock tests
MOCK_TEST_BLUEPRINTS = [
    # ── 1. JEE MAIN FULL MOCKS (3 Tests) ─────────────────────────────────────
    {
        "name": "JEE Main Full Mock Test #1",
        "exam_target": "jee",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "Standard JEE Main Pattern. 90 Questions. Section A (MCQs) has negative marking (+4/-1). Section B (Numerical Value/Integer) has no negative marking (+4/0).",
        "sections": [
            {"name": "Physics - Section A (MCQ)", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Physics - Section B (Integer)", "subject": "Physics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Chemistry - Section A (MCQ)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Chemistry - Section B (Integer)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Mathematics - Section A (MCQ)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Mathematics - Section B (Integer)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
        ]
    },
    {
        "name": "JEE Main Full Mock Test #2",
        "exam_target": "jee",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "Standard JEE Main Pattern. 90 Questions. Section A (MCQs) has negative marking (+4/-1). Section B (Numerical Value/Integer) has no negative marking (+4/0).",
        "sections": [
            {"name": "Physics - Section A (MCQ)", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Physics - Section B (Integer)", "subject": "Physics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Chemistry - Section A (MCQ)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Chemistry - Section B (Integer)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Mathematics - Section A (MCQ)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Mathematics - Section B (Integer)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
        ]
    },
    {
        "name": "JEE Main Full Mock Test #3",
        "exam_target": "jee",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "Standard JEE Main Pattern. 90 Questions. Section A (MCQs) has negative marking (+4/-1). Section B (Numerical Value/Integer) has no negative marking (+4/0).",
        "sections": [
            {"name": "Physics - Section A (MCQ)", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Physics - Section B (Integer)", "subject": "Physics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Chemistry - Section A (MCQ)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Chemistry - Section B (Integer)", "subject": "Chemistry", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
            {"name": "Mathematics - Section A (MCQ)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Mathematics - Section B (Integer)", "subject": "Mathematics", "pool": "jee_main", "q_types": ["integer"], "count": 10, "marks_correct": 4.0, "marks_incorrect": 0.0},
        ]
    },

    # ── 2. JEE ADVANCED FULL MOCKS (2 Tests) ─────────────────────────────────
    # Uses mini pattern (18 questions per test) due to the size of Chemistry and Math pools (12 questions each)
    {
        "name": "JEE Advanced Full Mock Test #1",
        "exam_target": "jee",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "JEE Advanced Pattern. 18 high-fidelity questions. Section A (Single Correct) (+3/-1). Section B (Multi-Correct) (+4/-2 with partial marks). Section C (Integer) (+3/0).",
        "sections": [
            {"name": "Physics - MCQ (Single Correct)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Physics - MCQ (Multi Correct)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Physics - Numerical (Integer)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
            {"name": "Chemistry - MCQ (Single Correct)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Chemistry - MCQ (Multi Correct)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Chemistry - Numerical (Integer)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
            {"name": "Mathematics - MCQ (Single Correct)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Mathematics - MCQ (Multi Correct)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Mathematics - Numerical (Integer)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
        ]
    },
    {
        "name": "JEE Advanced Full Mock Test #2",
        "exam_target": "jee",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "JEE Advanced Pattern. 18 high-fidelity questions. Section A (Single Correct) (+3/-1). Section B (Multi-Correct) (+4/-2 with partial marks). Section C (Integer) (+3/0).",
        "sections": [
            {"name": "Physics - MCQ (Single Correct)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Physics - MCQ (Multi Correct)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Physics - Numerical (Integer)", "subject": "Physics", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
            {"name": "Chemistry - MCQ (Single Correct)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Chemistry - MCQ (Multi Correct)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Chemistry - Numerical (Integer)", "subject": "Chemistry", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
            {"name": "Mathematics - MCQ (Single Correct)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["single_correct"], "count": 2, "marks_correct": 3.0, "marks_incorrect": -1.0},
            {"name": "Mathematics - MCQ (Multi Correct)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["multi_correct"], "count": 2, "marks_correct": 4.0, "marks_incorrect": -2.0},
            {"name": "Mathematics - Numerical (Integer)", "subject": "Mathematics", "pool": "jee_advanced", "q_types": ["integer"], "count": 2, "marks_correct": 3.0, "marks_incorrect": 0.0},
        ]
    },

    # ── 3. NEET FULL MOCKS (2 Tests) ─────────────────────────────────────────
    {
        "name": "NEET Full Mock Test #1",
        "exam_target": "neet",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "Standard NEET Mock Test. 90 Questions. Single-Correct MCQs (+4/-1). Covering Physics, Chemistry, and Biology.",
        "sections": [
            {"name": "Physics Section", "subject": "Physics", "pool": "neet", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Chemistry Section", "subject": "Chemistry", "pool": "neet", "q_types": ["single_correct"], "count": 10, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Biology Section", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 50, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Full Mock Test #2",
        "exam_target": "neet",
        "test_type": "full_mock",
        "duration_mins": 180,
        "instructions": "Standard NEET Mock Test. 90 Questions. Single-Correct MCQs (+4/-1). Covering Physics, Chemistry, and Biology.",
        "sections": [
            {"name": "Physics Section", "subject": "Physics", "pool": "neet", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Chemistry Section", "subject": "Chemistry", "pool": "neet", "q_types": ["single_correct"], "count": 10, "marks_correct": 4.0, "marks_incorrect": -1.0},
            {"name": "Biology Section", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 50, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },

    # ── 4. SUBJECT-SPECIFIC MOCKS (7 Tests) ──────────────────────────────────
    {
        "name": "JEE Physics Subject Mock",
        "exam_target": "jee",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for JEE Physics. 30 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Physics MCQ", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "JEE Chemistry Subject Mock",
        "exam_target": "jee",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for JEE Chemistry. 30 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Chemistry MCQ", "subject": "Chemistry", "pool": "jee_main", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "JEE Mathematics Subject Mock",
        "exam_target": "jee",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for JEE Mathematics. 20 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Mathematics MCQ", "subject": "Mathematics", "pool": "jee_main", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Physics Subject Mock",
        "exam_target": "neet",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for NEET Physics. 20 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Physics MCQ", "subject": "Physics", "pool": "neet", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Chemistry Subject Mock",
        "exam_target": "neet",
        "test_type": "subject_mock",
        "duration_mins": 30,
        "instructions": "Full syllabus mock test for NEET Chemistry. 8 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Chemistry MCQ", "subject": "Chemistry", "pool": "neet", "q_types": ["single_correct"], "count": 8, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Biology Subject Mock #1",
        "exam_target": "neet",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for NEET Biology. 30 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Biology MCQ", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Biology Subject Mock #2",
        "exam_target": "neet",
        "test_type": "subject_mock",
        "duration_mins": 60,
        "instructions": "Full syllabus mock test for NEET Biology. 30 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Biology MCQ", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 30, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },

    # ── 5. UNIT / CHAPTER GROUP MOCKS (8 Tests) ──────────────────────────────
    {
        "name": "JEE Physics Unit Mock - Mechanics",
        "exam_target": "jee",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for JEE Physics - Mechanics chapters (Kinematics, Laws of Motion, Work Energy Power, Rotational). 15 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Physics MCQ", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 15, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "JEE Physics Unit Mock - Electromagnetism",
        "exam_target": "jee",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for JEE Physics - Electromagnetism chapters (Electrostatics, Current Electricity, Magnetism, EMI & AC). 15 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Physics MCQ", "subject": "Physics", "pool": "jee_main", "q_types": ["single_correct"], "count": 15, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "JEE Chemistry Unit Mock - Physical & Inorganic",
        "exam_target": "jee",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for JEE Chemistry - Physical & Inorganic chapters (Basic Concepts, Atomic Structure, Bonding, s-Block, Solutions). 15 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Chemistry MCQ", "subject": "Chemistry", "pool": "jee_main", "q_types": ["single_correct"], "count": 15, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "JEE Mathematics Unit Mock - Calculus & Algebra",
        "exam_target": "jee",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for JEE Maths - Calculus & Algebra (Limits, Continuity, Differentiation, Matrices, Determinants). 15 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Mathematics MCQ", "subject": "Mathematics", "pool": "jee_main", "q_types": ["single_correct"], "count": 15, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Biology Unit Mock - Cell & Genetics",
        "exam_target": "neet",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for NEET Biology - Cell Structure, Division, and Genetics. 20 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Biology MCQ", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Biology Unit Mock - Ecology & Physiology",
        "exam_target": "neet",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for NEET Biology - Ecology and Physiology. 20 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Biology MCQ", "subject": "Biology", "pool": "neet", "q_types": ["single_correct"], "count": 20, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Physics Unit Mock - Mechanics & Heat",
        "exam_target": "neet",
        "test_type": "unit_mock",
        "duration_mins": 45,
        "instructions": "Unit mock test for NEET Physics - Mechanics & Thermal Physics. 15 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Physics MCQ", "subject": "Physics", "pool": "neet", "q_types": ["single_correct"], "count": 15, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    },
    {
        "name": "NEET Chemistry Unit Mock - Organic & Inorganic",
        "exam_target": "neet",
        "test_type": "unit_mock",
        "duration_mins": 30,
        "instructions": "Unit mock test for NEET Chemistry - Organic and Inorganic concepts. 10 Questions. MCQ Single-Correct (+4/-1).",
        "sections": [
            {"name": "Chemistry MCQ", "subject": "Chemistry", "pool": "neet", "q_types": ["single_correct"], "count": 10, "marks_correct": 4.0, "marks_incorrect": -1.0},
        ]
    }
]

def generate_mock_tests(conn=None):
    """
    Generates 22 mock test templates. Idempotent — skips templates that already
    exist by name. Never deletes existing sessions or question_results.
    Accepts an optional open connection; opens its own if not provided.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")
        close_conn = True
    cursor = conn.cursor()

    # Check how many templates already exist — if we already have 22, skip entirely
    existing_names = {
        r[0] for r in cursor.execute("SELECT name FROM test_templates").fetchall()
    }
    blueprints_to_create = [b for b in MOCK_TEST_BLUEPRINTS if b["name"] not in existing_names]
    if not blueprints_to_create:
        log.info("mock_test_generator: all 22 templates already exist, skipping.")
        if close_conn:
            conn.close()
        return

    log.info(f"mock_test_generator: creating {len(blueprints_to_create)} missing templates...")

    # Select only new questions with fingerprints
    cursor.execute("""
        SELECT q.id, q.exam_target, q.question_type, q.difficulty, q.tags, s.name as subject_name
        FROM questions q
        JOIN subjects s ON q.subject_id = s.id
        WHERE q.fingerprint IS NOT NULL AND q.is_active = 1
    """)
    
    questions = []
    for row in cursor.fetchall():
        q_id, exam_target, q_type, difficulty, tags_str, subject_name = row
        tags = json.loads(tags_str) if (tags_str and tags_str.startswith('[')) else [tags_str]
        questions.append({
            "id": q_id,
            "exam_target": exam_target.lower(),
            "q_type": q_type,
            "difficulty": difficulty,
            "tags": tags,
            "subject": subject_name,
            "use_count": 0
        })

    log.info(f"Loaded {len(questions)} new questions.")

    # Group questions into pools for easy lookup
    pools = {
        "jee_main": {"Physics": [], "Chemistry": [], "Mathematics": []},
        "jee_advanced": {"Physics": [], "Chemistry": [], "Mathematics": []},
        "neet": {"Physics": [], "Chemistry": [], "Biology": []}
    }

    for q in questions:
        target = q["exam_target"]
        subject = q["subject"]
        tags = q["tags"]
        
        # Determine pool
        if target == "jee":
            if "jee_advanced" in tags:
                if subject in pools["jee_advanced"]:
                    pools["jee_advanced"][subject].append(q)
            elif "jee_main" in tags or "jee_main" in str(tags):
                if subject in pools["jee_main"]:
                    pools["jee_main"][subject].append(q)
            else:
                # Fallback to main if unclassified
                if subject in pools["jee_main"]:
                    pools["jee_main"][subject].append(q)
        elif target == "neet":
            if subject in pools["neet"]:
                pools["neet"][subject].append(q)

    # Log pool sizes
    log.info("Pool sizes:")
    for pool_name, subj_dict in pools.items():
        for subj, q_list in subj_dict.items():
            log.info(f"  Pool '{pool_name}' - {subj}: {len(q_list)} questions")

    # Generate each blueprint
    templates_created = 0
    templates_failed = []
    for bp in blueprints_to_create:
        name = bp["name"]
        exam_target = bp["exam_target"]
        test_type = bp["test_type"]
        duration_mins = bp["duration_mins"]
        instructions = bp["instructions"]

        log.info(f"Generating template: '{name}'...")

        # Per-template SAVEPOINT: if this template fails for any reason (e.g.
        # a duplicate question slipping past dedup, or any other unexpected
        # DB error), we roll back ONLY this template's partial inserts and
        # move on to the next one. Previously a single failure here would
        # raise an uncaught exception and silently abort every template
        # still left in the loop -- this is what caused some students to see
        # far fewer mock tests than expected (e.g. NEET templates missing
        # because they were queued after a JEE template that crashed first).
        cursor.execute("SAVEPOINT template_gen")
        try:
            template_questions_batch = []
            global_q_order = 1
            total_marks = 0

            for sec_idx, sec in enumerate(bp["sections"], 1):
                sec_name = sec["name"]
                subject = sec["subject"]
                pool_name = sec["pool"]
                q_types = sec["q_types"]
                count = sec["count"]
                marks_correct = sec["marks_correct"]
                marks_incorrect = sec["marks_incorrect"]

                pool = pools[pool_name][subject]
                candidates = [q for q in pool if q["q_type"] in q_types]

                if len(candidates) < count:
                    log.warning(f"  [{name}] Pool '{pool_name}' for {subject} with type {q_types} is too small! Need {count}, only have {len(candidates)}. Allowing minimal repeats.")
                    candidates = list(pool)

                # De-duplicate by question id BEFORE selecting -- prevents the
                # same question id appearing twice in this template, which
                # used to crash on the UNIQUE(template_id, question_id)
                # constraint and silently kill every template after it.
                seen_ids = set()
                unique_candidates = []
                for q in candidates:
                    if q["id"] not in seen_ids:
                        seen_ids.add(q["id"])
                        unique_candidates.append(q)
                candidates = unique_candidates

                random.shuffle(candidates)
                candidates.sort(key=lambda x: x["use_count"])

                if len(candidates) < count:
                    log.warning(f"  [{name}] Only {len(candidates)} DISTINCT questions available for {subject} "
                                f"(needed {count}) -- section will have {len(candidates)} questions instead of {count}.")
                    selected = candidates
                else:
                    selected = candidates[:count]

                for q in selected:
                    q["use_count"] += 1
                    template_questions_batch.append({
                        "question_id": q["id"],
                        "section_name": sec_name,
                        "section_order": sec_idx,
                        "question_order": global_q_order,
                        "marks_correct": marks_correct,
                        "marks_incorrect": marks_incorrect
                    })
                    global_q_order += 1
                    total_marks += marks_correct

            marking_scheme_json = json.dumps({
                "correct": 4.0 if exam_target == "neet" or "Main" in name else 3.0,
                "incorrect": -1.0,
                "partial": 2.0
            })

            cursor.execute("""
                INSERT INTO test_templates (
                    name, exam_target, test_type, duration_mins, total_marks,
                    marking_scheme, instructions, is_published
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (name, exam_target.lower(), test_type, duration_mins, int(total_marks), marking_scheme_json, instructions))

            template_id = cursor.lastrowid

            # INSERT OR IGNORE is a second layer of defense beyond the dedup
            # above -- if a duplicate (template_id, question_id) pair ever
            # does slip through, this row is silently skipped instead of
            # raising and aborting the whole template.
            for tq in template_questions_batch:
                cursor.execute("""
                    INSERT OR IGNORE INTO template_questions (
                        template_id, question_id, section_name, section_order,
                        question_order, marks_correct, marks_incorrect
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    template_id, tq["question_id"], tq["section_name"],
                    tq["section_order"], tq["question_order"],
                    tq["marks_correct"], tq["marks_incorrect"]
                ))

            cursor.execute("RELEASE SAVEPOINT template_gen")
            templates_created += 1

        except Exception as e:
            # Roll back only this template's partial work and continue to
            # the next template instead of crashing the whole run.
            cursor.execute("ROLLBACK TO SAVEPOINT template_gen")
            cursor.execute("RELEASE SAVEPOINT template_gen")
            log.error(f"  [{name}] FAILED to generate -- skipping this template. Reason: {e}")
            templates_failed.append(name)

    conn.commit()
    if close_conn:
        conn.close()
    log.info(f"mock_test_generator: created {templates_created} new templates successfully.")
    if templates_failed:
        log.error(f"mock_test_generator: {len(templates_failed)} template(s) FAILED and were skipped: {templates_failed}")
        log.error("  These templates will be retried automatically on the next server restart.")

if __name__ == "__main__":
    generate_mock_tests()
