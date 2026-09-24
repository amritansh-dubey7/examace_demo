"""
ExamAce Question Seeder — Production Grade
==========================================
Seeds the SQLite database with questions from all question banks.
Maps the bank schema to the actual DB column names.

Run:
    python -m backend.services.question_seeder [--validate]

Idempotent: running multiple times inserts only missing questions.
"""

import sqlite3
import json
import os
import sys
import hashlib
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ── INSERT SQL (matches actual DB schema) ────────────────────────────────────
_INSERT_SQL = """
INSERT INTO questions (
    exam_target, subject_id, chapter_id,
    question_type, difficulty, difficulty_score,
    question_text,
    option_a, option_b, option_c, option_d,
    correct_answer, solution_text, tags,
    population_avg_time_secs, population_accuracy,
    fingerprint
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


# ── helpers ──────────────────────────────────────────────────────────────────
def _question_hash(text: str, subject: str, chapter: str) -> str:
    raw = f"{subject}|{chapter}|{text[:200]}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _get_or_create_subject(cursor, name: str) -> int:
    cursor.execute("SELECT id FROM subjects WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    # Try with exam_target column
    try:
        cursor.execute("INSERT INTO subjects (name, exam_target) VALUES (?, ?)",
                       (name, "jee" if name in ("Physics","Chemistry","Mathematics") else "neet"))
    except sqlite3.OperationalError:
        cursor.execute("INSERT INTO subjects (name) VALUES (?)", (name,))
    return cursor.lastrowid


def _get_or_create_chapter(cursor, subject_id: int, name: str) -> int:
    cursor.execute(
        "SELECT id FROM chapters WHERE subject_id = ? AND name = ?",
        (subject_id, name)
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(
        "INSERT INTO chapters (subject_id, name) VALUES (?, ?)",
        (subject_id, name)
    )
    return cursor.lastrowid


def _ensure_fingerprint_column(cursor):
    cursor.execute("PRAGMA table_info(questions)")
    cols = [r[1] for r in cursor.fetchall()]
    if "fingerprint" not in cols:
        cursor.execute("ALTER TABLE questions ADD COLUMN fingerprint TEXT")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_fingerprint ON questions(fingerprint)")
        log.info("Added 'fingerprint' column.")


# ── core seeder ──────────────────────────────────────────────────────────────
def seed_bank(cursor, bank: dict, exam_tag: str,
              *, existing_hashes: set, batch: list) -> tuple:
    """Map bank entries → DB rows. Returns (inserted, skipped)."""
    inserted = skipped = 0

    for (subject_name, chapter_name), chapter_data in bank.items():
        subject_id = _get_or_create_subject(cursor, subject_name)
        chapter_id = _get_or_create_chapter(cursor, subject_id, chapter_name)

        for q in chapter_data.get("questions", []):
            text        = q.get("text", "").strip()
            fingerprint = _question_hash(text, subject_name, chapter_name)

            if fingerprint in existing_hashes:
                skipped += 1
                continue
            existing_hashes.add(fingerprint)

            # Tags
            raw_tags = list(q.get("tags", []))
            if exam_tag not in raw_tags:
                raw_tags = [exam_tag] + raw_tags

            # Options (list → 4 individual columns)
            options = q.get("options") or []
            opt_a = str(options[0]) if len(options) > 0 else ""
            opt_b = str(options[1]) if len(options) > 1 else ""
            opt_c = str(options[2]) if len(options) > 2 else ""
            opt_d = str(options[3]) if len(options) > 3 else ""

            batch.append((
                exam_tag,                                    # exam_target
                subject_id,                                  # subject_id
                chapter_id,                                  # chapter_id
                q.get("q_type", "single_correct"),          # question_type
                q.get("difficulty", "medium"),              # difficulty
                float(q.get("difficulty_score", 0.50)),     # difficulty_score
                text,                                        # question_text
                opt_a, opt_b, opt_c, opt_d,                 # option_a..d
                str(q.get("answer", "")),                   # correct_answer
                q.get("solution", ""),                      # solution_text
                json.dumps(raw_tags),                        # tags
                int(q.get("avg_time", 90)),                 # population_avg_time_secs
                float(q.get("accuracy", 0.50)),             # population_accuracy
                fingerprint,                                 # fingerprint
            ))
            inserted += 1

    return inserted, skipped


def _get_banks():
    from backend.services.question_bank.jee_physics import JEE_PHYSICS_BANK
    from backend.services.question_bank.jee_chemistry import JEE_CHEMISTRY_BANK
    from backend.services.question_bank.jee_maths import JEE_MATHS_BANK
    from backend.services.question_bank.neet_physics_chemistry import (
        NEET_PHYSICS_BANK, NEET_CHEMISTRY_BANK
    )
    from backend.services.question_bank.neet_biology import (
        NEET_BOTANY_BANK, NEET_ZOOLOGY_BANK
    )
    return [
        ("JEE Physics",    JEE_PHYSICS_BANK,    "jee"),
        ("JEE Chemistry",  JEE_CHEMISTRY_BANK,  "jee"),
        ("JEE Maths",      JEE_MATHS_BANK,      "jee"),
        ("NEET Physics",   NEET_PHYSICS_BANK,   "neet"),
        ("NEET Chemistry", NEET_CHEMISTRY_BANK, "neet"),
        ("NEET Botany",    NEET_BOTANY_BANK,    "neet"),
        ("NEET Zoology",   NEET_ZOOLOGY_BANK,   "neet"),
    ]


# ── template seeder ──────────────────────────────────────────────────────────

# Official exam templates with exact real-exam structure.
# Each entry: (name, exam_target, test_type, duration_mins, sections)
# sections: list of (section_name, subject_name, n_questions, marks_correct, marks_incorrect)
# NOTE: mock_test_generator.py seeds the 22 realistic session-wise mocks automatically.
# _TEMPLATE_SPECS here provides only the subject-test and chapter-test templates
# that mock_test_generator does not cover.
_TEMPLATE_SPECS = [
    (
        "JEE Main — Physics Subject Test",
        "jee", "subject_test", 60,
        [("Physics", "Physics", 30, 4.0, -1.0)],
    ),
    (
        "JEE Main — Chemistry Subject Test",
        "jee", "subject_test", 60,
        [("Chemistry", "Chemistry", 30, 4.0, -1.0)],
    ),
    (
        "JEE Main — Mathematics Subject Test",
        "jee", "subject_test", 60,
        [("Mathematics", "Mathematics", 30, 4.0, -1.0)],
    ),
    (
        "NEET — Biology Subject Test",
        "neet", "subject_test", 90,
        [("Biology", "Biology", 90, 4.0, -1.0)],
    ),
    (
        "Quick Practice — 15 Questions (JEE)",
        "jee", "chapter_test", 30,
        [
            ("Mixed JEE", "Physics",     5, 4.0, -1.0),
            ("Mixed JEE", "Chemistry",   5, 4.0, -1.0),
            ("Mixed JEE", "Mathematics", 5, 4.0, -1.0),
        ],
    ),
    (
        "Quick Practice — 15 Questions (NEET)",
        "neet", "chapter_test", 30,
        [
            ("Mixed NEET", "Physics",   5, 4.0, -1.0),
            ("Mixed NEET", "Chemistry", 5, 4.0, -1.0),
            ("Mixed NEET", "Biology",   5, 4.0, -1.0),
        ],
    ),
]

def _get_subject_id(cursor, subject_name: str) -> int | None:
    """Return subject id by name (case-insensitive partial match)."""
    cursor.execute(
        "SELECT id FROM subjects WHERE lower(name) LIKE lower(?)",
        (f"%{subject_name}%",)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def seed_templates(conn):
    """
    Seeds official exam mock templates and links them to real questions from
    the question bank. Idempotent — skips templates that already exist by name.
    Must be called AFTER seed_questions() so the question bank is populated.
    Does NOT close the connection.
    """
    cursor = conn.cursor()

    # Guard: nothing to link if questions table is empty
    total_q = cursor.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    if total_q == 0:
        log.warning("seed_templates: question bank is empty, skipping template seeding.")
        return

    # Get existing template names to stay idempotent
    existing_names = {
        r[0] for r in cursor.execute("SELECT name FROM test_templates").fetchall()
    }

    templates_created = 0
    templates_skipped = 0

    for (name, exam_target, test_type, duration_mins, sections) in _TEMPLATE_SPECS:

        if name in existing_names:
            templates_skipped += 1
            continue

        # Calculate total_marks and build marking_scheme from sections
        total_marks = sum(n * mc for (_, _, n, mc, _) in sections)
        marking_scheme = json.dumps({
            "correct": sections[0][3],
            "incorrect": sections[0][4],
            "partial": 0,
        })

        cursor.execute(
            """INSERT INTO test_templates
               (name, exam_target, test_type, duration_mins, total_marks,
                marking_scheme, is_published)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (name, exam_target, test_type, duration_mins, total_marks, marking_scheme),
        )
        template_id = cursor.lastrowid

        # Link questions to this template section by section
        q_order = 1
        section_order = 1
        all_linked_q_ids: set = set()  # prevent duplicate question_id within a template

        for (section_name, subject_name, n_questions, marks_correct, marks_incorrect) in sections:

            subject_id = _get_subject_id(cursor, subject_name)
            if subject_id is None:
                log.warning(
                    f"  seed_templates: subject '{subject_name}' not found for "
                    f"template '{name}' section '{section_name}'. Skipping section."
                )
                section_order += 1
                continue

            # Pull questions for this section: balanced difficulty, random order
            # Aim for ~40% easy, 40% medium, 20% hard (realistic exam distribution)
            n_easy   = max(1, round(n_questions * 0.40))
            n_medium = max(1, round(n_questions * 0.40))
            n_hard   = max(0, n_questions - n_easy - n_medium)

            already_ids = list(all_linked_q_ids) or [0]
            placeholders = ",".join("?" * len(already_ids))

            def fetch_by_diff(diff, limit):
                return cursor.execute(
                    f"""SELECT id FROM questions
                        WHERE subject_id=? AND difficulty=?
                          AND exam_target=? AND is_active=1
                          AND id NOT IN ({placeholders})
                        ORDER BY RANDOM() LIMIT ?""",
                    (subject_id, diff, exam_target, *already_ids, limit),
                ).fetchall()

            picked_ids = []
            for diff, limit in [("easy", n_easy), ("medium", n_medium), ("hard", n_hard)]:
                rows = fetch_by_diff(diff, limit)
                picked_ids.extend(r[0] for r in rows)
                all_linked_q_ids.update(r[0] for r in rows)
                # Refresh exclusion list after each batch
                already_ids = list(all_linked_q_ids) or [0]
                placeholders = ",".join("?" * len(already_ids))

            # If we came up short (sparse bank), top up with any difficulty
            shortfall = n_questions - len(picked_ids)
            if shortfall > 0:
                extra_ids_list = list(all_linked_q_ids) or [0]
                extra_ph = ",".join("?" * len(extra_ids_list))
                extras = cursor.execute(
                    f"""SELECT id FROM questions
                        WHERE subject_id=? AND exam_target=? AND is_active=1
                          AND id NOT IN ({extra_ph})
                        ORDER BY RANDOM() LIMIT ?""",
                    (subject_id, exam_target, *extra_ids_list, shortfall),
                ).fetchall()
                picked_ids.extend(r[0] for r in extras)
                all_linked_q_ids.update(r[0] for r in extras)

            # Insert into template_questions
            for qid in picked_ids:
                try:
                    cursor.execute(
                        """INSERT INTO template_questions
                           (template_id, question_id, section_name, section_order,
                            question_order, marks_correct, marks_incorrect)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (template_id, qid, section_name, section_order,
                         q_order, marks_correct, marks_incorrect),
                    )
                    q_order += 1
                except sqlite3.IntegrityError:
                    # UNIQUE(template_id, question_id) violated — skip duplicate
                    pass

            section_order += 1

        conn.commit()
        linked = cursor.execute(
            "SELECT COUNT(*) FROM template_questions WHERE template_id=?",
            (template_id,)
        ).fetchone()[0]
        log.info(f"  Template '{name}': {linked} questions linked across {section_order-1} section(s).")
        templates_created += 1

    if templates_created:
        log.info(f"Template seeding done: {templates_created} created, {templates_skipped} already existed.")
    else:
        log.info(f"Template seeding: all {templates_skipped} templates already exist, nothing to do.")


# ── public API (called by database.py init_db) ───────────────────────────────
def seed_questions(conn):
    """
    Called by database.py init_db() with an open connection.
    Does NOT close the connection.
    Seeds questions first, then seeds official exam templates.
    Both operations are fully idempotent.
    """
    cursor = conn.cursor()
    _ensure_fingerprint_column(cursor)
    conn.commit()

    cursor.execute(
        "SELECT COALESCE(fingerprint,'') FROM questions WHERE fingerprint IS NOT NULL")
    existing_hashes: set = {r[0] for r in cursor.fetchall()}

    try:
        banks = _get_banks()
    except ImportError as e:
        log.warning(f"Could not load question banks: {e}. Skipping.")
        return

    batch: list = []
    total_ins = total_skp = 0
    for name, bank, exam_tag in banks:
        ins, skp = seed_bank(cursor, bank, exam_tag,
                             existing_hashes=existing_hashes, batch=batch)
        total_ins += ins
        total_skp += skp
        log.info(f"  {name:20s} → {ins} new, {skp} skipped")

    if batch:
        try:
            conn.executemany(_INSERT_SQL, batch)
            conn.commit()
            log.info(f"Seeding done: {total_ins} inserted, {total_skp} skipped.")
        except sqlite3.Error as e:
            conn.rollback()
            log.error(f"Seeding failed: {e}")
            return  # don't try to seed templates if questions failed
    else:
        log.info("No new questions to insert.")

    # Always run template seeder after questions (idempotent)
    seed_templates(conn)


# ── standalone entry point ────────────────────────────────────────────────────
def seed_all():
    from backend.models.database import DB_PATH
    log.info(f"DB: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    seed_questions(conn)
    conn.close()
    log.info("Done.")


def validate_banks():
    try:
        banks = _get_banks()
    except ImportError as e:
        print(f"Import error: {e}")
        return
    print("\n" + "=" * 60)
    print(" ExamAce Question Bank Validation")
    print("=" * 60)
    total = 0
    for name, bank, _ in banks:
        chapters  = len(bank)
        questions = sum(len(v.get("questions", [])) for v in bank.values())
        total += questions
        print(f"  {name:22s}: {chapters:3d} chapters, {questions:5d} questions")
    print("-" * 60)
    print(f"  {'GRAND TOTAL':22s}: {total:5d} questions")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ExamAce Question Seeder")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    validate_banks()
    if not args.validate:
        seed_all()
