# CareerPilot AI — Zero-to-Deploy 開發計畫

## Context

從零開發 SRS 定義的 CareerPilot AI（履歷 × 職缺 AI 平台），本計畫覆蓋從空目錄到本地 Docker 可重現交付的完整路徑，對齊 SER 594 課程要求（15+ tests、CI、RAG、agent、structured output、memory、quantitative eval、baseline）。

## 技術棧（已確認）

| 層級 | 選擇 |
|---|---|
| 前端 | Vite + React 18 + TypeScript + TanStack Query + Tailwind |
| 後端 | FastAPI (Python 3.11) + SQLAlchemy 2 + Alembic + Pydantic v2 |
| DB | PostgreSQL 16 + pgvector |
| LLM / Embedding | Google Gemini (`gemini-1.5-pro` / `text-embedding-004`) |
| Agent | LangGraph |
| Auth | JWT (access + refresh) + bcrypt |
| Deploy | docker-compose（frontend / backend / db 三服務） |
| CI | GitHub Actions |
| Test | pytest（後端）、Vitest + React Testing Library（前端） |

## Repo 結構（一次到位建好）

```
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
│   │   │   ├── parsers/    # resume / job structured parsing
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
│   ├── baselines/          # keyword-only matcher etc.
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

## Phase 0 — 專案初始化與基礎建設（Week 1, 約 3-5 天）

**目標**：可以 `docker compose up` 看到 hello world 前後端連通、CI pass、DB 可連線。

1. `git init`、建立 GitHub repo（private）、加 `.gitignore`（Python / Node / .env）
2. 寫 `.env.example`：`GEMINI_API_KEY`、`DATABASE_URL`、`JWT_SECRET`、`CORS_ORIGINS`
3. `docker-compose.yml`：
   - `db`：`pgvector/pgvector:pg16`，volume 持久化
   - `backend`：FastAPI uvicorn
   - `frontend`：Vite dev server
4. Backend skeleton：FastAPI app、`/health` endpoint、SQLAlchemy engine、Alembic init、`CREATE EXTENSION vector`
5. Frontend skeleton：Vite + React + TS、Tailwind、axios client、ping `/health`
6. CI（`.github/workflows/ci.yml`）：
   - lint（ruff + mypy、eslint + tsc）
   - backend pytest（含 Postgres service container）
   - frontend `vitest run`
7. README：setup / run / test 指令
8. `pre-commit` hooks（ruff、eslint、secret scan）

**驗收**：`docker compose up` → 前端打後端 `/health` 成功；`git push` → CI 綠燈。

---

## Phase 1 — 認證與資料層骨架（Week 2, 約 4-6 天）

**對應 SRS**：FR-1~6、FR-4 isolation、NFR-6~8、entity 定義 §5.1

1. DB models（SQLAlchemy）：`User`、`Resume`、`ResumeVersion`、`Job`、`JobChunk`、`JobEmbedding`（含 `vector(768)` 欄）、`MatchResult`、`SkillGapReport`、`GeneratedArtifact`、`UserPreference`、`ApplicationHistory`、`AIFeedback`。全部帶 `user_id` FK 並建 index。
2. Alembic 初始 migration（含 pgvector ivfflat index）
3. Auth：
   - `POST /auth/register`、`POST /auth/login`（JWT access+refresh）、`POST /auth/logout`（blacklist）、`GET /auth/me`
   - bcrypt 雜湊、`get_current_user` 依賴注入
   - Middleware：所有 `/api/*` 強制驗證（auth routes 除外）
4. 前端：
   - Register / Login 頁、AuthContext、axios interceptor（attach token、401 refresh）
   - Protected route wrapper
5. Tests（目標 6+）：register、login happy/fail、me、protected route 401、user isolation（A 使用者看不到 B 的資料）、refresh token flow

**驗收**：兩個 browser profile 分別註冊登入，看到各自獨立空 dashboard。

---

## Phase 2 — Gemini Wrapper + Structured Output 基建（Week 3 前半, 約 2-3 天）

**對應**：FR-59~65、6.4

1. `app/ai/llm/base.py`：`LLMProvider` abstract（`generate`, `generate_structured(schema)`, `embed`）
2. `app/ai/llm/gemini.py`：封裝 Gemini API，支援：
   - JSON mode（`response_mime_type="application/json"` + `response_schema`）
   - Retry（tenacity，指數退避）
   - Token / latency / cost 紀錄（寫 `GeneratedArtifact.meta` 或 dedicated `LLMCallLog`）
   - Timeout、streaming 預留
3. `app/ai/embeddings/gemini.py`：`text-embedding-004`（768 維），batch 呼叫
4. Structured output 測試：定義一個 toy schema（e.g. `{name, age}`），驗證 schema validation + retry + fallback parser（修破 JSON）
5. `LLMCallLog` table：provider、model、prompt_hash、tokens_in/out、latency_ms、cost_estimate、status

**驗收**：單元測試呼叫 Gemini、回傳符合 schema 物件；故意回亂字串觸發 retry + fallback 成功。

---

## Phase 3 — 履歷 ingestion 與解析（Week 3 後半, 約 3-4 天）

**對應**：FR-7~12

1. Upload endpoint：`POST /resumes/upload`（multipart，支援 PDF / DOCX / text）
2. 文字抽取：`pypdf` / `python-docx`，存原文到 `Resume.raw_text`
3. Structured parsing service：用 Gemini structured output 抽出 basic info / summary / skills / experience / projects / education / certifications（Pydantic schema）
4. Retry + fallback：解析失敗存 `parse_error`、回前端「請手動修正」
5. `ResumeVersion`：每次編輯存一版，支援回復
6. API：`GET /resumes/current`、`PATCH /resumes/{id}`、`GET /resumes/{id}/versions`
7. 前端 Resume Management 頁：上傳、展示 parsed fields、手動編輯 form、版本列表
8. Tests（3+）：upload happy path、parse 失敗 fallback、versioning

**驗收**：上傳 sample PDF → 看到結構化欄位 → 編輯 skills → 存新版本。

---

## Phase 4 — 職缺 ingestion + 向量索引（Week 4 前半, 約 3-4 天）

**對應**：FR-13~18

1. `POST /jobs` / `POST /jobs/upload`：接文字或檔案
2. Job 解析 service（structured output）：company / title / responsibilities / required_skills / preferred_skills / qualifications / experience / location
3. Chunking：section-aware（responsibilities 一段、required skills 一段…），保留 `section` metadata，約 300-500 tokens / chunk
4. Embedding：每 chunk 打 Gemini embedding → 寫 `JobEmbedding.vector`
5. pgvector 索引（ivfflat cosine）
6. API：`GET /jobs`、`GET /jobs/{id}`、`DELETE /jobs/{id}`
7. 前端 Job Management 頁：貼文字、列表、詳細頁
8. Tests（2+）：job 解析、向量寫入與相似度查詢

**驗收**：新增 3 個 job → DB 有 chunks + embeddings → 手動 SQL 查 cosine 相似度正確。

---

## Phase 5 — Match Ranking + Explanation（Week 4 後半, 約 3 天）

**對應**：FR-19~23

1. Match service 演算法（hybrid）：
   - Embedding similarity（resume summary/skills 向量 vs job chunks 平均/最大）
   - Required skill coverage（normalized string match + Gemini semantic equivalence fallback）
   - Preferred skill coverage
   - Experience alignment（年資、title 語意比對）
   - 加權合成 `match_score`（例：0.35/0.3/0.15/0.2）
2. `POST /matches/run`（body: resume_id, job_ids[]）→ 批次計算存 `MatchResult`
3. Explanation：LLM 根據結構化分數與 skill diff 生 structured explanation（why_matched, top_overlap, missing_skills, risks）
4. `GET /matches?resume_id=...` 排序回傳
5. 前端：Dashboard 展示 ranked list、每筆展開看 explanation
6. Tests（2+）：score 計算 deterministic 部分、API 流程

**驗收**：對 5 個 job 跑 match → 排序合理、explanation 有 skill overlap。

---

## Phase 6 — RAG Skill Gap Analysis（Week 5 前半, 約 3-4 天）

**對應**：FR-24~30（RAG minimum expectation：chunk / retrieve / rerank / generate w/ citation）

1. Retrieval：用 resume skills 為 query → 對該 job 的 chunks 做 top-k pgvector search
2. Rerank：cross-encoder（`sentence-transformers/ms-marco-MiniLM-L-6-v2` 本地跑）或 LLM rerank（比較後擇一，推薦 cross-encoder 省成本）
3. Generation：傳 retrieved chunks + resume structured → Gemini structured output 生 `SkillGapReport`：
   - gaps: [{skill, severity (high/med/low), evidence_chunk_ids, suggestion}]
4. Source attribution：回傳 chunk_id → 前端 hover 顯示原文
5. `POST /jobs/{id}/skill-gap`、`GET /skill-gaps/{id}`
6. 前端：Job Detail 頁「Skill Gap Analysis」tab，citation 可展開
7. Tests（2+）：retrieval top-k、citation 完整性

**驗收**：對一個 job 跑 skill gap → 每條 gap 都能點到 source chunk。

---

## Phase 7 — Agent Workflow: Application Kit（Week 5 後半 + Week 6 前半, 約 5-6 天）

**對應**：FR-31~44、FR-56~58（至少 3 tools + conditional logic）

1. LangGraph agent 設計：
   - **State**：`{resume, job, match_result, skill_gaps, tailored_resume, cover_letter, interview_prep, artifacts}`
   - **Tools（≥3）**：
     1. `fetch_resume(user_id)` — DB 查履歷
     2. `retrieve_job_evidence(job_id, query)` — RAG 檢索
     3. `compute_match(resume, job)` — 呼叫 match service
     4. `generate_tailored_resume(...)` — Gemini structured
     5. `generate_cover_letter(...)` — Gemini structured（intro/body/closing）
     6. `generate_interview_qs(...)` — Gemini structured
     7. `save_artifact(...)` — 寫 `GeneratedArtifact`
   - **Conditional edges**：
     - 若 `match_score < 0.5` → 先跑 skill_gap → 再 tailor resume
     - 若 `match_score >= 0.8` → 跳過 heavy gap analysis，直接 cover letter + interview prep
     - 任何 tool failure → 走 fallback node 記錄並 degrade（不整個 crash）
2. Endpoint：`POST /jobs/{id}/generate-application-kit` → 跑 agent → 回 artifacts
3. Artifact 儲存與版本控
4. 前端 Application Kit 頁：三區塊（resume suggestions / cover letter / interview prep），均可編輯、匯出（copy / .md）
5. Tests（3+）：agent 完整流程、conditional branch、tool failure degrade

**驗收**：一次 click 產出三類 artifact，conditional branching 可在 log 看到分岔。

---

## Phase 8 — Preferences + Memory + Application Tracking（Week 6 後半, 約 3-4 天）

**對應**：FR-45~55

1. Preferences CRUD：`GET/PUT /preferences`（target roles, locations, remote mode, industries, tone, skills_to_strengthen）
2. Memory 注入：agent state 自動 load preferences，ranking / cover letter / interview 都帶入
3. Application Tracking：`POST/PATCH /applications`（status enum、notes、timeline）
4. Feedback：`POST /feedback`（artifact_id、rating 1-5、comment）→ 存 `AIFeedback`
5. 前端：Preferences 頁、Application Tracker 頁（timeline + status board）
6. Cross-session 驗證（登出再登入資料還在）
7. Tests（2+）：preference reuse、application CRUD

**驗收**：設定偏好後產出的 cover letter tone / 鎖定技能有反映偏好。

---

## Phase 9 — Evaluation Layer（Week 7, 約 5 天）

**對應**：ER-1~5、NFR-1、NFR-5

1. **Curated dataset**（`eval/datasets/`）：手刻 25 組 `{resume, jobs[], ground_truth_ranking, ground_truth_gaps}`，JSON/YAML
2. **AI metrics**：
   - `Precision@K` 與 `MRR`（matching ranking）
   - Resume suggestion **rubric scorer**（LLM-as-judge + 固定 rubric：relevance / specificity / actionability / alignment；1-5 分）
   - 選配：skill gap recall vs ground truth
3. **Baselines**：
   - Keyword-only TF-IDF matcher
   - 無 RAG（直接 LLM without retrieval）產生 skill gap 當 baseline
4. **System metrics**：p50/p95 latency（resume parse / job index / match / kit gen）、error rate、test coverage（pytest-cov + c8）
5. `eval/run_eval.py`：一鍵跑全套、輸出 `docs/eval_report.md`（含表格 + baseline 對比）
6. CI 加 nightly eval job（可選）

**驗收**：`python eval/run_eval.py` 產出 report，主 metric 比 baseline 高。

---

## Phase 10 — 測試補齊 + 硬化（Week 8 前半, 約 3 天）

1. 清點測試數到 **≥15**（auth / api / data / ai pipeline 各領域都覆蓋）
2. Integration test：完整 e2e flow（register → upload resume → add job → match → kit）用 `httpx.AsyncClient` + test DB
3. 錯誤處理：LLM timeout、embedding fail、DB down → graceful degradation + 可讀錯誤
4. Rate limiting（slowapi）、input size limit、file type whitelist
5. Secret scan（gitleaks）加入 CI
6. Logging：structured JSON log、request id
7. Frontend：loading / error states、long task progress、表單驗證

**驗收**：`pytest -q` 綠、coverage ≥70%、刻意斷網測試不 crash。

---

## Phase 11 — Docker 打包 + 文件 + 交付（Week 8 後半 ~ Week 9, 約 3-5 天）

**對應**：NFR-14~15、Acceptance §10

1. Production Dockerfiles（multi-stage、non-root user、slim base）
2. `docker-compose.prod.yml`：frontend nginx serve build、backend gunicorn+uvicorn workers、db volume
3. `.env.example` 完整 + README quick-start（`cp .env.example .env` → 填 `GEMINI_API_KEY` → `docker compose up`）
4. `docs/architecture.md`：高階架構圖 + 資料流
5. `docs/api.md`：OpenAPI 自動生 + 人寫說明
6. `docs/eval_report.md`：metrics + baseline + screenshots
7. Demo 素材：2-3 條示範履歷 + 5 條 job seeds（`scripts/seed.py`）
8. README 最終版：features、screenshots、setup、test、eval 指令
9. Tag `v1.0.0`

**驗收**：乾淨環境 `git clone` + `docker compose up` + seed → 完整 demo flow 可走完。

---

## 驗收清單（對照 SRS §10）

- [ ] 註冊/登入/隔離（Phase 1）
- [ ] 履歷上傳 + 可編輯 structured parsing（Phase 3）
- [ ] Job 匯入 + 索引（Phase 4）
- [ ] Match ranking + explanation（Phase 5）
- [ ] Skill gap + source attribution（Phase 6）
- [ ] Resume suggestions / cover letter / interview prep（Phase 7）
- [ ] Cross-session 偏好與申請歷史（Phase 8）
- [ ] ≥3 AI techniques（RAG / agent / structured output / embeddings / memory ✓ 全中）
- [ ] ≥15 tests + CI（Phase 10 + 全程累積）
- [ ] Docker 重現（Phase 11）
- [ ] ≥2 quantitative metrics + baseline（Phase 9）
- [ ] 非 chatbot / 非 single API call（整體架構滿足）

## 風險與緩解

| Risk | Mitigation |
|---|---|
| Gemini structured output 不穩 | schema validation + retry + fallback JSON repair（Phase 2 內建） |
| pgvector index 小資料集效果差 | 先 exact search、資料量 > 1k 才切 ivfflat |
| Agent 無限迴圈 | LangGraph step limit + timeout |
| Token 成本爆炸 | Phase 2 token log、長文先摘要、cache resume 向量 |
| Eval dataset 太花時間 | Week 1 就開始零散蒐集 real job posts、Week 7 統一整理 |

## 每週節奏（建議）

| Week | 焦點 |
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
| 10 | Demo 錄製 / 報告撰寫 / 最終修復 |

## 驗證（交付前手動 end-to-end 測試）

1. `docker compose down -v && docker compose up --build` — 全新環境可跑
2. 註冊 user A → 上傳 sample PDF → 結構化解析正確
3. 新增 3 個 jobs → match → ranking 合理 → explanation 有意義
4. 對 top job 跑 application kit → 三類 artifact 產出 + citation 可點
5. 登出 → 登入 user B → 完全看不到 A 的資料
6. `pytest -q` 全綠、`npm test` 全綠
7. `python eval/run_eval.py` → 產出 report，主 metric > baseline
8. Push → GitHub Actions CI 綠燈
