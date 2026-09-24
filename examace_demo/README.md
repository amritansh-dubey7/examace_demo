# ExamAce — Phase 7: Adaptive Spaced Repetition

AI-powered exam prep platform for IIT-JEE, NEET, and UPSC preparation.

## What's New in Phase 7

### Adaptive Spaced Repetition (SM-2 Algorithm)

Phase 7 adds a full spaced repetition revision system that auto-populates from classified
mistakes after every test and uses the scientifically-proven SM-2 algorithm to schedule
reviews at the optimal moment before forgetting.

#### New: `backend/services/revision.py` — RevisionService

- **`auto_create_from_session(session_id)`** — Called automatically by the pipeline after
  every test. Reads classified question results and creates revision items:
  - `conceptual` / `formula_recall` mistakes → due tomorrow, risk = `high`
  - `avoidance` mistakes → due today, risk = `critical`
  - `was_revisited` AND correct → due in 3 days, risk = `medium`
  - All other wrong answers → due tomorrow, risk = `high`
  - Deduplication: if question already in queue, keeps the earlier due_date and resets interval to 1
- **`review_item(item_id, quality_label)`** — Full SM-2 update with three quality inputs:
  - `got_it` (quality=5): Perfect recall → longer interval
  - `with_hint` (quality=3): Correct with hint → moderate interval
  - `forgot` (quality=1): Blackout → resets to interval=1
- **`get_due_today(student_id)`** — Returns items due today + overdue, ordered:
  `critical` → `high` → `medium` → `low`, then by due_date ascending
- **`get_all(student_id)`** — Full queue for a student
- **`delete_item(item_id)`** — Remove an item

#### SM-2 Algorithm (Wozniak, 1987)

```
if quality < 3:
    repetition_count = 0
    interval = 1         ← always reset on failure
else:
    rep 0 → interval=1
    rep 1 → interval=6
    rep N → interval = round(prev_interval × ease_factor)
    repetition_count += 1

ease_factor += 0.1 - (5-quality) × (0.08 + (5-quality) × 0.02)
ease_factor  = max(1.3, ease_factor)
```

Forgetting risk thresholds:
| Interval      | Risk       |
|---------------|------------|
| > 14 days     | `low`      |
| > 5 days      | `medium`   |
| > 1 day       | `high`     |
| = 1 day       | `critical` |

#### New Database Table: `revision_queue`

```sql
CREATE TABLE revision_queue (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id         INTEGER REFERENCES students(id),
    chapter_id         INTEGER REFERENCES chapters(id),
    question_id        INTEGER REFERENCES questions(id),
    item_type          TEXT    DEFAULT 'question',      -- question|concept|formula
    description        TEXT    NOT NULL,
    due_date           TEXT    NOT NULL,
    interval_days      INTEGER DEFAULT 1,
    ease_factor        REAL    DEFAULT 2.5,
    repetition_count   INTEGER DEFAULT 0,
    last_reviewed_at   TEXT,
    forgetting_risk    TEXT    DEFAULT 'high',          -- critical|high|medium|low
    source_session_id  INTEGER REFERENCES test_sessions(id),
    created_at         TEXT    DEFAULT (datetime('now'))
);
```

Indexes: `(student_id, due_date)` and `(student_id, forgetting_risk)` for fast daily queries.

#### New API Routes

| Method   | Route                              | Description                                    |
|----------|------------------------------------|------------------------------------------------|
| `GET`    | `/api/revision/{sid}`              | Items due today + overdue (critical first)     |
| `GET`    | `/api/revision/{sid}/all`          | All revision items for student                 |
| `POST`   | `/api/revision/{item_id}/review`   | SM-2 review `{quality: got_it|with_hint|forgot}` |
| `DELETE` | `/api/revision/{item_id}`          | Remove an item from queue                      |

#### Pipeline Hook (backend/services/pipeline.py)

The `_revision_queue_hook()` stub (which was a no-op since Phase 3) is now wired to call
`RevisionService(student_id).auto_create_from_session(session_id)` after every test submission.

#### Frontend Changes (frontend/index.html)

- **"Due for Revision Today" section** added at the TOP of the Study Planner page, above the
  4-week calendar. Shows:
  - Chapter name, truncated question description (80 chars)
  - Color-coded risk badge (🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Low)
  - Interval and repetition count tags
  - Three action buttons: ✅ **Got It** / 💡 **With Hint** / ❌ **Forgot**
  - Each button fires SM-2 update inline (no full page reload) — card disappears, list refreshes
- **Nav badge** on "Study Planner" nav item showing count of items due today in red
- Badge updates on login, after every test, and after reviewing items
- `loadRevisionCount()` called on app startup and after test submission

## Architecture

```
examace/
  backend/
    main.py                          FastAPI app + all routes (incl. Phase 7 /api/revision/*)
    models/database.py               SQLite schema — now includes revision_queue table
    services/
      revision.py                    ← NEW Phase 7: RevisionService + SM-2 algorithm
      analytics.py
      behavioral.py
      digital_twin.py
      pipeline.py                    ← Phase 7 hook now active
      test_session_service.py
      question_seeder.py
      mock_test_generator.py
      question_bank/
  frontend/index.html                Single-page app with revision UI in buildPlanner()
  data/examace.db                    Auto-created SQLite
  requirements.txt
  start.sh
```

## Setup & Run

```bash
pip install -r requirements.txt
bash start.sh
# or
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000

## Testing Phase 7 (SM-2 Verification)

Per the master prompt: **"The SM-2 algorithm must be tested with all three quality inputs
before phase is considered complete."**

1. Complete at least one live test (conceptual/avoidance mistakes will populate the queue)
2. Open **Study Planner** — the revision section appears at the top
3. Click **Got It** on an item → interval increases (e.g., 1→6 days), risk becomes `medium`
4. Click **With Hint** on an item → moderate increase (e.g., 1→6 days), ease_factor unchanged
5. Click **Forgot** on an item → interval resets to 1, risk becomes `critical`, due tomorrow
6. Verify via `GET /api/revision/{sid}/all` that all three quality paths produce distinct outcomes

## Phase History

| Phase | What Was Built                                              |
|-------|-------------------------------------------------------------|
| 1     | Question bank (240 questions), test templates               |
| 2     | Live test interface, EventManager, behavioral event capture |
| 3     | Behavioral analysis engine, mistake classification pipeline |
| 4     | Digital Twin (7 cognitive scores), progressive unlock gates |
| 5     | AI Mentor upgrade with Digital Twin context                 |
| 6     | Rank Intelligence Dashboard (5 sections, unlock gated)      |
| 7     | **Adaptive Spaced Repetition — SM-2 revision queue**        |
| 8     | Adaptive Study Planner (intelligence-driven daily plan)     |
| 9     | Deep Performance Review (5-tab post-test analysis screen)   |

---

## Phase 7.1 — Follow-up Fixes (Post-Review)

Three issues were raised after initial Phase 7 delivery. All three are now fixed.

### 1. Improved Visual Flash on Revision Review

The flash panel shown after clicking ✅ Got It / 💡 With Hint / ❌ Forgot was upgraded from
a flat "Next review in N days" message to a richer feedback panel:

- Quality-specific headline ("Perfect recall!", "Got it with a hint", "Forgot — that's okay")
- An animated ease-factor progress bar (visualizes how close to "easy" the item is, 1.3–3.5 range)
- A short motivational line tailored to the outcome (e.g. "🔥 3 reps in — this is getting locked in")
- **Dynamic hold time** instead of one fixed 1.4s for everything:
  - `got_it` → 1.0s (student is confident, keep momentum)
  - `with_hint` → 1.2s
  - `forgot` → 1.8s (give the student a moment to process the reset)

### 2. Subject-Filterable Performance Heatmap

`buildPerformance()` (Performance page) now shows subject tab buttons above the heatmap chart:

- **Physics is shown by default** on first load
- Tabs auto-detect which subjects exist for the student's exam target — JEE students see
  Physics / Chemistry / Mathematics, NEET students see Physics / Chemistry / Biology
- Clicking a tab (`setHeatmapSubject()`) filters the Chart.js bar chart to that subject's
  chapters only — no more all-subjects-mixed-together chart
- Selection persists in `state.heatmapSubject` across re-renders

### 3. "Correct but Ambiguous" Yellow Warning

Previously, the app only flagged *wrong* or *skipped* answers with mistake types. Right
answers were treated as uniformly solid — even if the student got there by guessing, after
flip-flopping between options, or after taking far longer than the question warranted.

**New backend detection** (`backend/services/behavioral.py` →
`MistakeClassifier.classify_ambiguous_correct()`), wired into the pipeline as **Step 9b**
(runs only on `is_correct=1` rows, fully separate from the existing wrong/skipped
classifier in Step 9):

| Reason | Trigger |
|---|---|
| `guessed_but_correct` | Answered very fast, zero answer changes, self-rated "guessing" |
| `too_long_for_right_answer` | Took much longer than population average + 2+ answer changes |
| `flip_flopped_to_correct` | 3+ answer changes before landing on the right option |

New columns on `question_results`: `is_ambiguous_correct` (bool), `ambiguous_reason` (text) —
added via safe `ALTER TABLE` migration, so existing databases upgrade automatically.

**Frontend**: the result page (`buildResultQuestionCard()`) shows a yellow warning banner
**only** on flagged questions — never on every correct answer. A new "⚠️ Needs Review (N)"
filter button appears next to All/Wrong/Skipped/Correct, but only when at least one ambiguous
item exists in that test.

