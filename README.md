# CareerPilot AI

CareerPilot AI is an AI-powered resume and job match platform for students, new graduates, and early-career professionals. The system allows a user to upload a resume and one or more target job descriptions, then analyzes job-fit relevance, highlights skill gaps with evidence, and generates tailored application materials such as resume improvement suggestions, cover letter drafts, and interview preparation questions. The platform is designed as a full-stack application with authentication, persistent storage, semantic retrieval, structured AI outputs, and cross-session user memory.

## Team Members

- [Min-Chi Hong] - GitHub: [mhong26]
- [Shih-I Tsai] - GitHub: [stsai124]
- [Amie Nguyen] - GitHub: [honganhnguyen-lab]
- [Hung-Ju Lin] - GitHub: [NSYSUHermit]

---

## Required Tools and Dependencies

### Docker setup (recommended)

| Tool | Version | Notes |
|---|---|---|
| Docker | >= 24 | [Install Docker](https://docs.docker.com/get-docker/) |
| Docker Compose | >= 2.x | Bundled with Docker Desktop |
| Git | any | |

### Local development (without Docker)

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| uv | latest | `pip install uv` or [install guide](https://github.com/astral-sh/uv) |
| Node.js | 20+ | [nodejs.org](https://nodejs.org/) |
| npm | 10+ | Bundled with Node.js |
| PostgreSQL | 16 + pgvector | Required for local backend |

### External accounts

| Service | Purpose |
|---|---|
| Google AI Studio | Gemini API key — [aistudio.google.com](https://aistudio.google.com/) |

---

## Development Environment Setup

### Quick Start with Docker (Recommended)

**1. Clone and configure:**
```bash
git clone <repo-url>
cd CareerPilot_AI
cp .env.example .env
```

**2. Fill in the required secrets in `.env`:**
```
GEMINI_API_KEY=your-gemini-api-key
JWT_SECRET=any-random-string-at-least-32-chars
```

**3. Start all services:**
```bash
docker compose up --build
```

**4. Run database migrations (first time only):**
```bash
docker compose exec backend alembic upgrade head
```

**5. Open in browser:**
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs

---

### Local Development (without Docker)

Requires a running PostgreSQL 16 instance with the pgvector extension available.

**Backend:**
```bash
cd backend
uv pip install -e ".[dev]"

# Set environment variables (or create a .env file in backend/)
export DATABASE_URL=postgresql://user:password@localhost:5432/careerpilot
export JWT_SECRET=your-secret
export GEMINI_API_KEY=your-key

# Run migrations
alembic upgrade head

# Start dev server
uvicorn app.main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

---

## Running Tests

**Backend:**
```bash
cd backend
pytest -v
```

**Frontend:**
```bash
cd frontend
npm test
```

**Run all checks (mirrors CI):**
```bash
# Backend lint + tests
cd backend
ruff check .
mypy app --ignore-missing-imports
pytest -v --cov=app

# Frontend lint + tests
cd frontend
npm run lint
npm run typecheck
npm test
```

---

## Project Structure

```
CareerPilot_AI/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers
│   │   ├── core/           # config, security, deps
│   │   ├── db/             # session, models, migrations
│   │   ├── schemas/        # Pydantic schemas
│   │   ├── services/       # business logic
│   │   └── ai/
│   │       ├── llm/        # Gemini wrapper
│   │       ├── embeddings/
│   │       ├── parsers/    # structured resume/job parsing
│   │       ├── rag/        # chunk, retrieve, rerank
│   │       ├── agents/     # LangGraph workflows
│   │       └── prompts/
│   ├── tests/
│   ├── alembic/            # database migrations
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/            # axios client + hooks
│   │   ├── lib/
│   │   └── types/
│   └── tests/
├── eval/
│   ├── datasets/           # 25+ curated profiles/jobs
│   ├── metrics/            # precision@k, mrr, rubric scorer
│   ├── baselines/          # keyword-only matcher
│   └── run_eval.py
├── docs/
├── .github/workflows/ci.yml
├── docker-compose.yml
└── .env.example
```
