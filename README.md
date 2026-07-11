# 🚀 Growza – AI-Powered Career Development Platform

Growza is an AI-powered career development platform that helps users bridge the gap between their current skills and their desired career path through personalized recommendations, intelligent analysis, and AI-driven learning.

The platform combines multiple AI services into one ecosystem, providing users with personalized career guidance, CV optimization, job matching, interview preparation, and portfolio building.

---

## ✨ Features

### 🤖 AI Career Builder
- Personalized learning roadmaps
- Skill gap analysis
- AI-generated weekly learning plans
- Learning timeline estimation
- Roadmap regeneration based on user feedback
- Progress tracking

### 📄 CV Analyzer
- CV parsing
- Technical skill extraction
- ATS optimization
- Proficiency estimation

### 💼 Hybrid Job Matching
- Semantic skill matching
- Rule-based recommendation engine
- Personalized job recommendations

### 🎤 AI Mock Interview
- Technical interview simulation
- Behavioral interview simulation
- AI-generated feedback

### 📊 Market Insights
- Salary analytics
- Labor market trends
- Career demand analysis

### 🌐 Portfolio Builder
- No-code portfolio creation
- Professional portfolio publishing

---

# 🏗️ Tech Stack

## Backend

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Supabase
- Redis
- Celery

## Artificial Intelligence

- Google Gemini
- Groq (Llama 3.3)
- NLP
- Semantic Search
- Embedding Models
- AI Recommendation System

## Cloud & Storage

- Azure Blob Storage
- Cloudinary

## Frontend

- Flutter

---

# ⚙️ Architecture

```
Flutter App
      │
      ▼
 FastAPI Backend
      │
 ├── Authentication
 ├── Career Builder
 ├── CV Analyzer
 ├── Job Matching
 ├── Mock Interview
 ├── Portfolio Builder
 ├── Market Insights
      │
      ▼
 PostgreSQL + Supabase
      │
      ▼
 Gemini / Groq APIs
```

---

# 👩‍💻 My Contribution

As the Backend Developer for the **Career Builder** module, I designed and implemented:

- CV analysis pipeline
- Skill extraction
- Semantic skill matching
- Skill gap analysis
- Learning timeline estimation
- AI-powered roadmap generation
- Personalized recommendation logic
- Feedback-based roadmap regeneration
- Progress persistence
- Backend APIs
- Database design and integration

I also contributed to designing the Hybrid Job Matching recommendation strategy.

---

# 🚀 Getting Started

## Clone the repository

```bash
git clone https://github.com/Shorouqtareq982/Advisor_Career_App.git
```

## Install dependencies

```bash
pip install -r requirements.txt
```

## Create .env

Copy:

```
.env.example
```

to

```
.env
```

and fill in your API keys.

## Run the project

```bash
uvicorn app.main:app --reload
```

---

# 📂 Project Structure

```
backend/
│
├── app/
├── routers/
├── services/
├── models/
├── schemas/
├── database/
├── utils/
├── ai/
└── main.py

frontend/

docs/

README.md
```

---

# 👥 Team

Faculty of Computers and Data Science  
Alexandria University

Graduation Project 2026

---

# 📜 License

This repository is intended for educational purposes as part of the Growza Graduation Project.
