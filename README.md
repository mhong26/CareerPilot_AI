# CareerPilot AI

[![CI](https://github.com/mhong26/CareerPilot_AI/actions/workflows/ci.yml/badge.svg?event=pull_request)](https://github.com/mhong26/CareerPilot_AI/actions/workflows/ci.yml)
[![CD](https://github.com/mhong26/CareerPilot_AI/actions/workflows/cd.yml/badge.svg)](https://github.com/mhong26/CareerPilot_AI/actions/workflows/cd.yml)

CareerPilot AI is an AI-powered resume and job match platform for students, new graduates, and early-career professionals. Upload a resume and one or more target job descriptions — the system analyzes job-fit relevance, highlights skill gaps with cited evidence, and generates tailored application materials: resume improvement suggestions, cover letter drafts, and interview preparation questions.

![Dashboard — ranked job matches](docs/images/dashboard.png)

---

## Features

- **Authentication & data isolation** — JWT (access + refresh rotation), bcrypt, per-user data scoping
- **Resume ingestion** — PDF / DOCX / pasted text → LLM structured parsing → editable fields → versioning
- **Job ingestion & indexing** — structured parsing → section-aware chunking → 768-dim embeddings → pgvector (HNSW, cosine)
- **Job match ranking** — hybrid score (embedding similarity + skill coverage + experience alignment) with LLM-generated explanations
- **Skill gap analysis (RAG)** — top-k retrieval + cross-encoder rerank → gap report with severity levels and clickable source citations
- **Application kit (agent)** — one click produces tailored resume suggestions, a cover letter draft, and interview prep questions
- **Evaluation layer** — six metric suites with baselines, one command, reproducible report
- **One-command deployment** — Docker images published to GHCR on every merge to `main` and every `v*` tag

## AI Techniques

| Technique | Where |
|---|---|
| **Structured outputs** | All parsing & generation uses schema-validated JSON with retry → repair → model fallback (`gemini-3.5-flash-lite` → `gemini-3.6-flash`) |
| **Embeddings + vector search** | `gemini-embedding-001` (768-dim, L2-normalized) + pgvector HNSW cosine index |
| **RAG** | chunk → retrieve → cross-encoder rerank → generate with source attribution |
| **Agent (LangGraph)** | 7-tool ReAct agent; the LLM picks tools via native function calling, with match-score conditional routing |
| **Function calling** | `ChatGoogleGenerativeAI.bind_tools` — tool selection is LLM-driven, not hard-coded |
| **LLM-as-judge evaluation** | Hallucination detection & rubric scoring via LangSmith `evaluate()` |
| **Observability** | LangSmith tracing on every wrapper call and agent run (silently disabled without an API key) |

## Screenshots

| Skill gap analysis (RAG + citations) | Application kit (agent output) |
|---|---|
| ![Skill gap](docs/images/skill-gap.png) | ![Application kit](docs/images/application-kit.png) |

More: [resume editing](docs/images/resume.png) · [job list](docs/images/jobs.png) · [job detail](docs/images/job-detail.png)

---

## Quick Start (Production, via GHCR)

Requires only Docker (>= 24) and two files from this repo — no source build needed.

```bash
git clone https://github.com/mhong26/CareerPilot_AI.git
cd CareerPilot_AI
cp .env.example .env
# Fill in .env:  GEMINI_API_KEY (aistudio.google.com)  +  JWT_SECRET (any random string ≥32 chars)

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Open **http://localhost** — database migrations run automatically on startup.

**Optional demo data** (2 demo accounts, 1 parsed resume each, 5 indexed jobs):

```bash
docker compose -f docker-compose.prod.yml exec backend python scripts/seed.py
# Log in as demo@careerpilot.ai / Demo1234!   (isolation demo: demo2@careerpilot.ai)
```

Published images (multi-arch: `linux/amd64` + `linux/arm64`):

| Image | Tags |
|---|---|
| `ghcr.io/mhong26/careerpilot-backend` | `latest`, `sha-<short-commit>`（7 碼，如 `sha-90dd603`）, `1.0.0` / `1.0` / `1` |
| `ghcr.io/mhong26/careerpilot-frontend` | `latest`, `sha-<short-commit>`（7 碼，如 `sha-90dd603`）, `1.0.0` / `1.0` / `1` |

Pin a version with `IMAGE_TAG=1.0.0` in `.env`. To update: `pull` again and `up -d`.

---

## Development Setup

### Required tools

| Tool | Version | Notes |
|---|---|---|
| Docker + Compose | >= 24 / 2.x | [Install Docker](https://docs.docker.com/get-docker/) |
| Python (no-Docker dev) | 3.11+ | plus `uv`: `pip install uv` |
| Node.js (no-Docker dev) | 20+ | npm bundled |
| Google AI Studio key | — | [aistudio.google.com](https://aistudio.google.com/) |

### Dev with Docker (hot reload)

```bash
cp .env.example .env        # fill GEMINI_API_KEY + JWT_SECRET
docker compose up --build
docker compose exec backend alembic upgrade head    # first time only
docker compose exec backend python scripts/seed.py  # optional demo data
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000 — Swagger at `/docs`

### Local development (without Docker)

Requires a running PostgreSQL 16 instance with the pgvector extension available.

**Backend:**
```bash
cd backend
uv pip install -e ".[dev]"

export DATABASE_URL=postgresql://user:password@localhost:5432/careerpilot
export JWT_SECRET=your-secret
export GEMINI_API_KEY=your-key

alembic upgrade head
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

```bash
# Backend (coverage gate: 80%)
cd backend
pytest -v --cov=app

# Frontend
cd frontend
npm test

# Full CI mirror
cd backend && ruff check . && mypy app --ignore-missing-imports && pytest -v --cov=app
cd frontend && npm run lint && npm run typecheck && npm test
```

CI runs on every push; CD (`cd.yml`) re-runs the full CI suite as a gate before building and publishing images on `main` / `v*` tags.

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

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System layers, data flows, agent graph, dev-vs-prod deployment topology |
| [docs/api_reference.md](docs/api_reference.md) | All REST endpoints (also live at `/docs` Swagger) |
| [docs/eval_report.md](docs/eval_report.md) | Metrics, baselines, hallucination rate, coverage |
| [docs/SRS.md](docs/SRS.md) | Software requirements specification |
| [docs/plan.md](docs/plan.md) | Phase-by-phase development plan |

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
│   │       ├── llm/        # Gemini wrapper (retry / repair / fallback)
│   │       ├── parsers/    # structured resume/job parsing
│   │       ├── rag/        # chunk, retrieve, rerank
│   │       ├── agents/     # LangGraph 7-tool agent
│   │       └── prompts/
│   ├── scripts/            # demo seed (frozen parsed data + real embeddings)
│   ├── tests/
│   ├── alembic/            # database migrations
│   ├── Dockerfile          # dev (bind mount + --reload)
│   └── Dockerfile.prod     # multi-stage, non-root, auto-migration + gunicorn
├── frontend/
│   ├── src/                # pages, components, api hooks
│   ├── tests/
│   ├── Dockerfile          # dev (Vite dev server)
│   ├── Dockerfile.prod     # static build served by non-root nginx
│   └── nginx.conf          # SPA fallback + /api reverse proxy
├── eval/                   # datasets, metrics, baselines, suites, run_eval.py
├── docs/
├── .github/workflows/      # ci.yml (lint/test) + cd.yml (GHCR publish)
├── docker-compose.yml      # dev stack
├── docker-compose.prod.yml # production stack (GHCR images)
└── .env.example
```
