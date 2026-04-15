# CareerPilot AI — Zero-to-Deploy Development Plan

## Context

Develop CareerPilot AI (an AI platform for resumes × job postings) defined by the SRS from scratch. This plan covers the complete path from an empty directory to a locally reproducible Docker delivery, aligned with the SER 594 course requirements (15+ tests, CI, RAG, agent, structured output, memory, quantitative evaluation, baseline).

## Tech Stack (Confirmed)

| Layer | Choice |
|---|---|
| Frontend | Vite + React 18 + TypeScript + TanStack Query + Tailwind |
| Backend | FastAPI (Python 3.11) + SQLAlchemy 2 + Alembic + Pydantic v2 |
| DB | PostgreSQL 16 + pgvector |
| LLM / Embedding | Google Gemini (`gemini-1.5-pro` / `text-embedding-004`) |
| Agent | LangGraph |
| Auth | JWT (access + refresh) + bcrypt |
| Deploy | docker-compose (frontend / backend / db three-service setup) |
| CI | GitHub Actions |
| Test | pytest (backend), Vitest + React Testing Library (frontend) |

## Repo Structure (Set It Up Properly in One Go)

```text
CareerPilot_AI/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers
│   │   ├── core/           # config, security, deps
│   │   ├── db/             # session, models, migrations
│   │   ├── schemas/        # Pydantic schemas
│   │   ├── services/       # business logic
│   │   ├── ai/
│   │   │   ├── llm/        # Gemini wrapper (provider-agnostic)
│   │   │   ├── embeddings/
│   │   │   ├── parsers/    # structured parsing for resumes / jobs
│   │   │   ├── rag/        # chunk, retrieve, rerank
│   │   │   ├── agents/     # LangGraph workflows
│   │   │   └── prompts/
│   │   └── main.py
│   ├── tests/
│   ├── alembic/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/            # axios client + hooks
│   │   ├── lib/
│   │   └── types/
│   ├── tests/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
├── eval/
│   ├── datasets/           # 25+ curated profiles/jobs
│   ├── metrics/            # precision@k, mrr, rubric scorer
│   ├── baselines/          # keyword-only matcher, etc.
│   └── run_eval.py
├── docs/
│   ├── architecture.md
│   ├── api.md
│   └── eval_report.md
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
├── README.md
└── CareerPilot_AI_SRS.md
```

---

## Phase 0 — Project Initialization and Infrastructure (Week 1, about 3–5 days)

**Goal**: Be able to run `docker compose up` and see hello world with frontend-backend connectivity, CI passing, and DB connectivity working.

1. `git init`, create GitHub repo (private), add `.gitignore` (Python / Node / .env)
2. Write `.env.example`: `GEMINI_API_KEY`, `DATABASE_URL`, `JWT_SECRET`, `CORS_ORIGINS`
3. `docker-compose.yml`:
   - `db`: `pgvector/pgvector:pg16`, with persistent volume
   - `backend`: FastAPI uvicorn
   - `frontend`: Vite dev server
4. Backend skeleton: FastAPI app, `/health` endpoint, SQLAlchemy engine, Alembic init, `CREATE EXTENSION vector`
5. Frontend skeleton: Vite + React + TS, Tailwind, axios client, ping `/health`
6. CI (`.github/workflows/ci.yml`):
   - lint (ruff + mypy, eslint + tsc)
   - backend pytest (including Postgres service container)
   - frontend `vitest run`
7. README: setup / run / test commands
8. `pre-commit` hooks (ruff, eslint, secret scan)

**Acceptance**: `docker compose up` → frontend successfully hits backend `/health`; `git push` → CI green.

---

## Phase 1 — Authentication and Data Layer Skeleton (Week 2, about 4–6 days)

**Mapped to SRS**: FR-1~6, FR-4 isolation, NFR-6~8, entity definitions §5.1

1. DB models (SQLAlchemy): `User`, `Resume`, `ResumeVersion`, `Job`, `JobChunk`, `JobEmbedding` (including `vector(768)` column), `MatchResult`, `SkillGapReport`, `GeneratedArtifact`, `UserPreference`, `ApplicationHistory`, `AIFeedback`. All include `user_id` FK and indexes.
2. Initial Alembic migration (including pgvector ivfflat index)
3. Auth:
   - `POST /auth/register`, `POST /auth/login` (JWT access+refresh), `POST /auth/logout` (blacklist), `GET /auth/me`
   - bcrypt hashing, `get_current_user` dependency injection
   - Middleware: enforce authentication for all `/api/*` routes (except auth routes)
4. Frontend:
   - Register / Login pages, AuthContext, axios interceptor (attach token, 401 refresh)
   - Protected route wrapper
5. Tests (target 6+): register, login happy/fail, me, protected route 401, user isolation (user A cannot see user B’s data), refresh token flow

**Acceptance**: Register and log in separately in two browser profiles, and each sees an independent empty dashboard.

---

## Phase 2 — Gemini Wrapper + Structured Output Foundation (First Half of Week 3, about 2–3 days)

**Mapped to**: FR-59~65, 6.4

1. `app/ai/llm/base.py`: `LLMProvider` abstract class (`generate`, `generate_structured(schema)`, `embed`)
2. `app/ai/llm/gemini.py`: wrap Gemini API, supporting:
   - JSON mode (`response_mime_type="application/json"` + `response_schema`)
   - Retry (tenacity, exponential backoff)
   - Token / latency / cost logging (write to `GeneratedArtifact.meta` or dedicated `LLMCallLog`)
   - Timeout, streaming reserved for later
3. `app/ai/embeddings/gemini.py`: `text-embedding-004` (768 dimensions), batch calls
4. Structured output tests: define a toy schema (e.g. `{name, age}`), verify schema validation + retry + fallback parser (repair broken JSON)
5. `LLMCallLog` table: provider, model, prompt_hash, tokens_in/out, latency_ms, cost_estimate, status

**Acceptance**: Unit test calls Gemini and returns an object conforming to the schema; intentionally returning garbled strings triggers retry + fallback successfully.

---

## Phase 3 — Resume Ingestion and Parsing (Second Half of Week 3, about 3–4 days)

**Mapped to**: FR-7~12

1. Upload endpoint: `POST /resumes/upload` (multipart, supports PDF / DOCX / text)
2. Text extraction: `pypdf` / `python-docx`, store original text in `Resume.raw_text`
3. Structured parsing service: use Gemini structured output to extract basic info / summary / skills / experience / projects / education / certifications (Pydantic schema)
4. Retry + fallback: if parsing fails, store `parse_error` and return “please edit manually” to frontend
5. `ResumeVersion`: save a version on every edit, supports rollback
6. API: `GET /resumes/current`, `PATCH /resumes/{id}`, `GET /resumes/{id}/versions`
7. Frontend Resume Management page: upload, display parsed fields, manual edit form, version list
8. Tests (3+): upload happy path, parse failure fallback, versioning

**Acceptance**: Upload a sample PDF → see structured fields → edit skills → save a new version.

---

## Phase 4 — Job Ingestion + Vector Indexing (First Half of Week 4, about 3–4 days)

**Mapped to**: FR-13~18

1. `POST /jobs` / `POST /jobs/upload`: accept text or file
2. Job parsing service (structured output): company / title / responsibilities / required_skills / preferred_skills / qualifications / experience / location
3. Chunking: section-aware (one chunk for responsibilities, one chunk for required skills, etc.), preserve `section` metadata, about 300–500 tokens / chunk
4. Embedding: run Gemini embedding for each chunk → write to `JobEmbedding.vector`
5. pgvector index (ivfflat cosine)
6. API: `GET /jobs`, `GET /jobs/{id}`, `DELETE /jobs/{id}`
7. Frontend Job Management page: paste text, list, detail page
8. Tests (2+): job parsing, vector write and similarity query

**Acceptance**: Add 3 jobs → DB contains chunks + embeddings → manual SQL cosine similarity query is correct.

---

## Phase 5 — Match Ranking + Explanation (Second Half of Week 4, about 3 days)

**Mapped to**: FR-19~23

1. Match service algorithm (hybrid):
   - Embedding similarity (resume summary/skills vectors vs average/max of job chunks)
   - Required skill coverage (normalized string match + Gemini semantic equivalence fallback)
   - Preferred skill coverage
   - Experience alignment (years of experience, title semantic matching)
   - Weighted composite `match_score` (example: 0.35/0.3/0.15/0.2)
2. `POST /matches/run` (body: resume_id, job_ids[]) → batch compute and store `MatchResult`
3. Explanation: LLM generates structured explanation based on structured scores and skill diff (why_matched, top_overlap, missing_skills, risks)
4. `GET /matches?resume_id=...` returns sorted results
5. Frontend: Dashboard displays ranked list, each item expandable to view explanation
6. Tests (2+): deterministic parts of score calculation, API flow

**Acceptance**: Run matching on 5 jobs → ranking is reasonable, explanation includes skill overlap.

---

## Phase 6 — RAG Skill Gap Analysis (First Half of Week 5, about 3–4 days)

**Mapped to**: FR-24~30 (RAG minimum expectation: chunk / retrieve / rerank / generate w/ citation)

1. Retrieval: use resume skills as query → run top-k pgvector search over that job’s chunks
2. Rerank: cross-encoder (`sentence-transformers/ms-marco-MiniLM-L-6-v2` run locally) or LLM rerank (compare and choose one; cross-encoder recommended to save cost)
3. Generation: pass retrieved chunks + structured resume → Gemini structured output generates `SkillGapReport`:
   - gaps: [{skill, severity (high/med/low), evidence_chunk_ids, suggestion}]
4. Source attribution: return chunk_id → frontend hover displays original text
5. `POST /jobs/{id}/skill-gap`, `GET /skill-gaps/{id}`
6. Frontend: “Skill Gap Analysis” tab on Job Detail page, citations expandable
7. Tests (2+): retrieval top-k, citation integrity

**Acceptance**: Run skill gap analysis on one job → every gap can be clicked to its source chunk.

---

## Phase 7 — Agent Workflow: Application Kit (Second Half of Week 5 + First Half of Week 6, about 5–6 days)

**Mapped to**: FR-31~44, FR-56~58 (at least 3 tools + conditional logic)

1. LangGraph agent design:
   - **State**: `{resume, job, match_result, skill_gaps, tailored_resume, cover_letter, interview_prep, artifacts}`
   - **Tools (≥3)**:
     1. `fetch_resume(user_id)` — query resume from DB
     2. `retrieve_job_evidence(job_id, query)` — RAG retrieval
     3. `compute_match(resume, job)` — call match service
     4. `generate_tailored_resume(...)` — Gemini structured
     5. `generate_cover_letter(...)` — Gemini structured (intro/body/closing)
     6. `generate_interview_qs(...)` — Gemini structured
     7. `save_artifact(...)` — write to `GeneratedArtifact`
   - **Conditional edges**:
     - If `match_score < 0.5` → run skill_gap first → then tailor resume
     - If `match_score >= 0.8` → skip heavy gap analysis, go straight to cover letter + interview prep
     - Any tool failure → go to fallback node for logging and degrade gracefully (no full crash)
2. Endpoint: `POST /jobs/{id}/generate-application-kit` → run agent → return artifacts
3. Artifact storage and version control
4. Frontend Application Kit page: three sections (resume suggestions / cover letter / interview prep), all editable and exportable (copy / .md)
5. Tests (3+): full agent flow, conditional branch, tool failure degrade

**Acceptance**: One click generates three types of artifacts, and the conditional branching can be seen in the logs.

---

## Phase 8 — Preferences + Memory + Application Tracking (Second Half of Week 6, about 3–4 days)

**Mapped to**: FR-45~55

1. Preferences CRUD: `GET/PUT /preferences` (target roles, locations, remote mode, industries, tone, skills_to_strengthen)
2. Memory injection: agent state automatically loads preferences, and ranking / cover letter / interview all incorporate them
3. Application Tracking: `POST/PATCH /applications` (status enum, notes, timeline)
4. Feedback: `POST /feedback` (artifact_id, rating 1-5, comment) → store in `AIFeedback`
5. Frontend: Preferences page, Application Tracker page (timeline + status board)
6. Cross-session verification (data still exists after logout and login again)
7. Tests (2+): preference reuse, application CRUD

**Acceptance**: After setting preferences, the generated cover letter tone / targeted skills reflect those preferences.

---

## Phase 9 — Evaluation Layer (Week 7, about 5 days)

**Mapped to**: ER-1~5, NFR-1, NFR-5

1. **Curated dataset** (`eval/datasets/`): handcraft 25 sets of `{resume, jobs[], ground_truth_ranking, ground_truth_gaps}`, in JSON/YAML
2. **AI metrics**:
   - `Precision@K` and `MRR` (matching ranking)
   - Resume suggestion **rubric scorer** (LLM-as-judge + fixed rubric: relevance / specificity / actionability / alignment; score 1–5)
   - Optional: skill gap recall vs ground truth
3. **Baselines**:
   - Keyword-only TF-IDF matcher
   - No RAG (direct LLM without retrieval) skill gap generation as baseline
4. **System metrics**: p50/p95 latency (resume parse / job index / match / kit generation), error rate, test coverage (pytest-cov + c8)
5. `eval/run_eval.py`: one-click full evaluation run, outputs `docs/eval_report.md` (including tables + baseline comparison)
6. Add nightly eval job to CI (optional)

**Acceptance**: `python eval/run_eval.py` produces a report, and the main metrics are higher than the baseline.

---

## Phase 10 — Test Completion + Hardening (First Half of Week 8, about 3 days)

1. Count tests to **≥15** (covering auth / api / data / ai pipeline domains)
2. Integration test: full e2e flow (register → upload resume → add job → match → kit) using `httpx.AsyncClient` + test DB
3. Error handling: LLM timeout, embedding failure, DB down → graceful degradation + readable errors
4. Rate limiting (slowapi), input size limit, file type whitelist
5. Add secret scan (gitleaks) to CI
6. Logging: structured JSON logs, request id
7. Frontend: loading / error states, long task progress, form validation

**Acceptance**: `pytest -q` green, coverage ≥70%, intentional offline/network-break tests do not crash.

---

## Phase 11 — Docker Packaging + Documentation + Delivery (Second Half of Week 8 ~ Week 9, about 3–5 days)

**Mapped to**: NFR-14~15, Acceptance §10

1. Production Dockerfiles (multi-stage, non-root user, slim base)
2. `docker-compose.prod.yml`: frontend built and served by nginx, backend with gunicorn+uvicorn workers, db volume
3. Complete `.env.example` + README quick-start (`cp .env.example .env` → fill in `GEMINI_API_KEY` → `docker compose up`)
4. `docs/architecture.md`: high-level architecture diagram + data flow
5. `docs/api.md`: OpenAPI auto-generated + manually written explanations
6. `docs/eval_report.md`: metrics + baseline + screenshots
7. Demo materials: 2–3 sample resumes + 5 job seeds (`scripts/seed.py`)
8. Final README: features, screenshots, setup, test, eval commands
9. Tag `v1.0.0`

**Acceptance**: In a clean environment, `git clone` + `docker compose up` + seed → the full demo flow can be completed.

---

## Acceptance Checklist (Against SRS §10)

- [ ] Registration / login / isolation (Phase 1)
- [ ] Resume upload + editable structured parsing (Phase 3)
- [ ] Job import + indexing (Phase 4)
- [ ] Match ranking + explanation (Phase 5)
- [ ] Skill gap + source attribution (Phase 6)
- [ ] Resume suggestions / cover letter / interview prep (Phase 7)
- [ ] Cross-session preferences and application history (Phase 8)
- [ ] ≥3 AI techniques (RAG / agent / structured output / embeddings / memory ✓ all included)
- [ ] ≥15 tests + CI (Phase 10 + cumulative throughout)
- [ ] Docker reproducibility (Phase 11)
- [ ] ≥2 quantitative metrics + baseline (Phase 9)
- [ ] Not a chatbot / not a single API call (overall architecture satisfies this)

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Gemini structured output is unstable | schema validation + retry + fallback JSON repair (built into Phase 2) |
| pgvector index performs poorly on small datasets | use exact search first, switch to ivfflat only when data volume > 1k |
| Agent enters an infinite loop | LangGraph step limit + timeout |
| Token costs explode | Phase 2 token logging, summarize long text first, cache resume vectors |
| Eval dataset takes too much time | start collecting real job posts incrementally in Week 1, consolidate in Week 7 |

## Weekly Cadence (Recommended)

| Week | Focus |
|---|---|
| 1 | Phase 0 |
| 2 | Phase 1 |
| 3 | Phase 2 + 3 |
| 4 | Phase 4 + 5 |
| 5 | Phase 6 + 7a |
| 6 | Phase 7b + 8 |
| 7 | Phase 9 |
| 8 | Phase 10 + 11a |
| 9 | Phase 11b + buffer |
| 10 | Demo recording / report writing / final fixes |

## Validation (Manual End-to-End Testing Before Delivery)

1. `docker compose down -v && docker compose up --build` — runs successfully in a fresh environment
2. Register user A → upload sample PDF → structured parsing is correct
3. Add 3 jobs → match → ranking is reasonable → explanation is meaningful
4. Run application kit on the top job → three artifact types are generated + citations are clickable
5. Log out → log in as user B → cannot see any of user A’s data at all
6. `pytest -q` all green, `npm test` all green
7. `python eval/run_eval.py` → produces report, main metric > baseline
8. Push → GitHub Actions CI green
