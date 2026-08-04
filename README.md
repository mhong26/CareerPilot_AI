# CareerPilot AI

CareerPilot AI is an AI-powered resume and job match platform for students, new graduates, and early-career professionals. The system allows a user to upload a resume and one or more target job descriptions, then analyzes job-fit relevance, highlights skill gaps with evidence, and generates tailored application materials such as resume improvement suggestions, cover letter drafts, and interview preparation questions. The platform is designed as a full-stack application with authentication, persistent storage, semantic retrieval, and structured AI outputs.

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

## Evaluation (Phase 8)

一鍵評估：`python eval/run_eval.py` 會（冪等地）建立獨立資料庫
`careerpilot_eval`、seed 25 組標註情境、跑六類指標並輸出
[`docs/eval_report.md`](docs/eval_report.md)。

```bash
# 前置：docker compose up -d db；.env 需含 GEMINI_API_KEY
pip install -e "backend[dev,eval]"          # eval extra = scikit-learn

python eval/run_eval.py                     # 全套（judge 類需 LANGSMITH_API_KEY）
python eval/run_eval.py --skip-judge        # 只跑本地指標（不需 LangSmith）
python eval/run_eval.py --limit 5           # 額度控管：每個 suite 只跑前 5 組情境
python eval/run_eval.py --only matching rag # 只跑指定 suite
python eval/run_eval.py --with-coverage     # 附帶量測 backend test coverage
python -m eval.langsmith.sync_datasets      # 本地 dataset 同步至 LangSmith
```

額度說明（free tier 友善）：所有 LLM / embedding 呼叫都有磁碟快取
（`eval/.cache/`，已 gitignore），重跑不重新計費；judge 模型
（`gemini-3.6-flash`，可用 `EVAL_JUDGE_MODEL` 覆寫）額度耗盡時 suite 標記
partial、隔日重跑自動續進度。無 `GEMINI_API_KEY` 時 warm 快取仍可離線重算
本地指標；無 `LANGSMITH_API_KEY` 時 judge 類跳過並於報告註明。

Eval 單元測試（不打 API、不碰 DB）：`python -m pytest eval/tests -q`。
Dataset 標註指南與設計理由見 [`eval/datasets/README.md`](eval/datasets/README.md)。

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
│   ├── datasets/           # 25 curated scenarios + annotation guideline
│   ├── metrics/            # precision@k, mrr, percentile（純函式）
│   ├── baselines/          # TF-IDF matcher、no-RAG gap baseline
│   ├── suites/             # matching / rag / hallucination / rubric / reliability / system
│   ├── langsmith/          # dataset sync + LLM-as-judge evaluators
│   ├── tests/              # eval 單元測試（CI 執行）
│   └── run_eval.py         # 一鍵評估 → docs/eval_report.md
├── docs/
├── .github/workflows/ci.yml
├── docker-compose.yml
└── .env.example
```
