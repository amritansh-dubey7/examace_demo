# ExamAce — AI-Powered Adaptive Exam Preparation

> **An intelligent learning platform that transforms student performance data into personalized, adaptive preparation.**

ExamAce is an AI-powered exam preparation platform designed to go beyond conventional mock-test applications.

Instead of simply telling students **what they scored**, ExamAce analyzes *how they learn*, identifies weaknesses and behavioral patterns, and provides personalized guidance based on each student's learning profile.

The project is being developed toward an **Agentic AI Learning Coach** capable of continuously observing student performance, reasoning about learning gaps, taking actions, and adapting the preparation strategy.

---

## 🚀 Vision

Millions of students prepare for competitive examinations without access to personalized mentorship.

ExamAce aims to make personalized academic guidance scalable.

The long-term vision is:

```text
Student
   ↓
Digital Twin
   ↓
AI Learning Agent
   ↓
Observe
   ↓
Diagnose
   ↓
Plan
   ↓
Act
   ↓
Evaluate
   ↓
Adapt
   ↺
```

Instead of providing the same preparation strategy to every student, ExamAce aims to give each learner a continuously adapting AI learning coach.

---

# ✨ Current Features

## 🧠 Student Digital Twin

ExamAce maintains a structured representation of a student's learning profile.

The Digital Twin tracks information such as:

* Topic mastery
* Cognitive/performance scores
* Accuracy
* Behavioral patterns
* Historical performance
* Recurring mistakes
* Learning trends

This information forms the basis for personalized recommendations.

---

## 📊 Deep Performance Analysis

ExamAce goes beyond simple marks and percentage calculations.

The system analyzes:

* Correct and incorrect answers
* Topic-level performance
* Question difficulty
* Recurring mistakes
* Behavioral patterns
* Performance trends

### Post-Test Analysis

After a test, students receive a detailed breakdown of their performance and areas requiring attention.

---

## 🔍 Mistake Classification

ExamAce analyzes incorrect responses and attempts to identify patterns behind mistakes.

Examples include:

* Conceptual weakness
* Repeated topic-level errors
* Difficulty-related errors
* Time-management issues
* Careless mistakes

This helps distinguish **"I got this question wrong"** from **"I don't understand this concept."**

---

## 📈 Performance Trend Analysis

ExamAce tracks performance over time using trend analysis.

This allows students to identify:

* Improving topics
* Declining performance
* Persistent weaknesses
* Changes in accuracy
* Long-term preparation trends

---

## 🔄 Spaced Repetition

ExamAce incorporates the **SM-2 spaced-repetition algorithm** to help schedule revision.

The system can prioritize concepts that need to be revisited based on previous performance.

---

## 📅 Adaptive Study Planner

The current platform includes a rule-based study planner that uses student performance information to generate personalized preparation recommendations.

The goal is to move from:

> "Here is your syllabus."

to:

> "Here is what you should work on next."

---

# 🤖 AI Mentor

ExamAce includes an AI Mentor that provides personalized academic guidance.

Unlike a generic chatbot, the AI Mentor receives a personalized context briefing based on the student's ExamAce data.

The AI can use information such as:

* Current strengths
* Weak topics
* Recent tests
* Performance trends
* Learning history
* Study recommendations

This allows conversations to remain grounded in the student's actual preparation.

### AI Architecture

The AI layer is provider-switchable.

Current support includes:

* **Groq-hosted open-source LLMs**
* **Anthropic Claude**

The architecture allows the underlying model provider to be changed without redesigning the rest of the application.

---

# 🧪 Assessment Platform

ExamAce includes an examination environment designed for competitive-exam preparation.

### Current content

* JEE question bank
* NEET question bank
* Examination-style mock tests

### Test system

Students can:

1. Select a test
2. Attempt questions
3. Submit the examination
4. Receive performance analysis
5. Review mistakes
6. Receive personalized recommendations

The test interface also captures relevant behavioral events for subsequent analysis.

---

# 🚀 Agentic AI Roadmap

The next major stage of ExamAce is transforming the existing AI Mentor into an **Agentic AI Learning Coach**.

The objective is to move from:

```text
Student → Ask AI → Receive Answer
```

toward:

```text
Student
   ↓
AI Learning Agent
   ↓
Observe student data
   ↓
Diagnose weaknesses
   ↓
Create plan
   ↓
Take learning actions
   ↓
Evaluate results
   ↓
Adapt plan
```

## Planned Agent Tools

The agent will be able to interact with ExamAce systems through tools such as:

### `get_student_performance()`

Retrieve the student's historical performance.

### `analyze_topic_mastery()`

Determine topic-level strengths and weaknesses.

### `retrieve_questions()`

Find questions based on:

* Topic
* Difficulty
* Previous mistakes
* Learning objective

### `generate_study_plan()`

Create a personalized preparation strategy.

### `update_study_plan()`

Modify the plan based on new performance.

### `get_revision_queue()`

Retrieve concepts scheduled for revision.

### `evaluate_progress()`

Compare new performance against previous performance and determine whether the learning strategy should change.

---

# 🧩 Agentic Learning Loop

The target architecture is:

```text
┌───────────────────────┐
│       Student         │
└───────────┬───────────┘
            ↓
┌───────────────────────┐
│    ExamAce Digital    │
│         Twin          │
└───────────┬───────────┘
            ↓
┌───────────────────────┐
│    AI Learning Agent  │
└───────────┬───────────┘
            ↓
      ┌─────────────┐
      │   Observe   │
      └──────┬──────┘
             ↓
      ┌─────────────┐
      │  Diagnose   │
      └──────┬──────┘
             ↓
      ┌─────────────┐
      │    Plan     │
      └──────┬──────┘
             ↓
      ┌─────────────┐
      │     Act     │
      └──────┬──────┘
             ↓
      ┌─────────────┐
      │  Evaluate   │
      └──────┬──────┘
             ↓
      ┌─────────────┐
      │    Adapt    │
      └──────┬──────┘
             │
             └───────────────↺
```

The agent will interact with the existing question bank, student-performance engine, revision system and study planner rather than functioning as an isolated chatbot.

---

# 🛠️ Technology Stack

## Frontend

* HTML
* CSS
* Vanilla JavaScript
* Single-page web application

## Backend

* Python
* FastAPI
* Uvicorn
* Pydantic
* HTTPX

## AI

* Groq API
* Open-source LLMs
* Anthropic Claude support
* Prompt-engineered contextual AI Mentor
* Persistent conversational memory

## Student Intelligence

* Digital Twin engine
* Topic mastery analysis
* Mistake classification
* Behavioral analysis
* Linear regression
* SM-2 spaced repetition
* Adaptive study planning

## Database

Current:

* SQLite

Planned:

* PostgreSQL

---

# 🏗️ Architecture

```text
                    ┌─────────────────────┐
                    │      Student        │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │    ExamAce Web UI   │
                    │ HTML/CSS/JavaScript  │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │     FastAPI API      │
                    └──────────┬──────────┘
                               ↓
              ┌────────────────┼────────────────┐
              ↓                ↓                ↓
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
      │ Digital Twin │ │ Test Engine  │ │ Study Planner│
      └──────┬───────┘ └──────┬───────┘ └──────────────┘
             │                │
             └────────┬───────┘
                      ↓
              ┌───────────────┐
              │   AI Mentor   │
              └───────┬───────┘
                      ↓
              ┌───────────────┐
              │  Groq / Claude│
              └───────────────┘
```

---

# 🔐 Environment Variables

Create a `.env` file in the backend/project root.

```env
GROQ_API_KEY=your_groq_api_key
```

If using Anthropic:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
```

**Never commit API keys to GitHub.**

Make sure `.env` is included in `.gitignore`.

Example:

```gitignore
.env
__pycache__/
*.pyc
```

---

# 💻 Installation

Clone the repository:

```bash
git clone <YOUR_REPOSITORY_URL>
cd ExamAce
```

Create a virtual environment:

### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure your environment variables:

```env
GROQ_API_KEY=your_key
```

---

# ▶️ Running the Application

Start the FastAPI server:

```bash
uvicorn main:app --reload
```

Depending on the project structure, the command may instead be:

```bash
uvicorn app.main:app --reload
```

Open the application in your browser:

```text
http://127.0.0.1:8000
```

FastAPI API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

---

# 📂 Project Structure

The exact structure may evolve, but the application is broadly organized around:

```text
ExamAce/
│
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── script.js
│
├── backend/
│   ├── main.py
│   ├── routes/
│   ├── services/
│   ├── models/
│   └── database/
│
├── data/
│   └── question_bank/
│
├── tests/
│
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

Adapt the structure above to the actual repository structure.

---

# 📌 Roadmap

## Current

* [x] Mock test engine
* [x] JEE/NEET question bank
* [x] Performance tracking
* [x] Digital Twin
* [x] Mistake analysis
* [x] Performance trends
* [x] SM-2 revision system
* [x] Rule-based study planner
* [x] AI Mentor
* [x] Personalized AI context
* [x] Persistent AI conversation memory
* [x] Groq integration

## Hackathon Development

* [ ] Agentic AI architecture
* [ ] Tool calling
* [ ] Student-data retrieval tools
* [ ] AI-driven question selection
* [ ] AI-generated adaptive study plans
* [ ] Automatic plan modification
* [ ] Continuous performance evaluation
* [ ] Next-best-action recommendation
* [ ] End-to-end autonomous learning loop

## Future

* [ ] RAG over previous-year questions
* [ ] IRT-based personalization
* [ ] PostgreSQL migration
* [ ] React + TypeScript frontend
* [ ] Multi-language learning support
* [ ] Additional competitive examinations
* [ ] Large-scale validation of learning recommendations

---

# 🌍 Building for Billions

Personalized education is traditionally expensive because it requires human mentors to continuously understand each student's strengths, weaknesses and progress.

ExamAce attempts to make this process scalable through AI.

The goal is not simply:

> **AI that answers students' questions.**

The goal is:

> **AI that understands a student's learning state and continuously helps decide what they should do next.**

With an agentic architecture, ExamAce can potentially provide personalized academic guidance to students who otherwise would not have access to individual mentorship.

---

# 🏆 Build for Billions

ExamAce is being developed for:

### **Agentic AI for Billions**

The proposed system demonstrates an agentic learning loop:

**Observe → Diagnose → Plan → Act → Evaluate → Adapt**

The existing ExamAce platform provides the underlying assessment, performance-analysis and personalization infrastructure. The hackathon development focuses on extending this foundation into an autonomous AI learning agent.

---

# 🤝 Contributing

Contributions, ideas and feedback are welcome.

Fork the repository, create a feature branch and submit a pull request.

```bash
git checkout -b feature/your-feature
git commit -m "Add your feature"
git push origin feature/your-feature
```

---

# 📄 License

Add your chosen license here.

Example:

```text
MIT License
```

---

# 👨‍💻 Team

**ExamAce**

Built at **National Institute of Technology Karnataka, Surathkal**

> **Personalized learning intelligence, accessible at scale.**
