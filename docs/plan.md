# CareerPilot AI — Zero-to-Deploy 開發計畫

## Context

從零開發 SRS 定義的 CareerPilot AI（履歷 × 職缺 AI 平台），本計畫覆蓋從空目錄到本地 Docker 可重現交付與 GHCR CI/CD 發佈的完整路徑。

> 進度註記：Phase 0~4 已完成；Phase 2R（retrofit）為對已完成部分的回補，須於 Phase 5 開始前執行。

## 技術棧（已確認）

| 層級 | 選擇 |
|---|---|
| 前端 | Vite + React 18 + TypeScript + TanStack Query + Tailwind |
| 後端 | FastAPI (Python 3.11) + SQLAlchemy 2 + Alembic + Pydantic v2 |
| DB | PostgreSQL 16 + pgvector |
| LLM | Gemini 3.5 Flash Lite（primary）+ Gemini 3.6 Flash（fallback），單一 `GEMINI_API_KEY` |
| Agent | LangGraph + langchain-google-genai（Gemini function calling / `bind_tools`） |
| Observability / Eval | LangSmith（`@traceable` tracing、datasets、`evaluate()`、LLM-as-judge） |
| Auth | JWT (access + refresh) + bcrypt |
| Deploy | docker-compose（dev）+ docker-compose.prod.yml（GHCR images） |
| CI/CD | GitHub Actions（lint / test）+ GHCR image publish（main / `v*` tags） |
| Test | pytest（後端）、Vitest + React Testing Library（前端） |

## Resume Bullet ↔ 文件對照表

| Resume bullet（摘要） | SRS 條款 | Plan Phase |
|---|---|---|
| Full-stack 平台：RAG + agentic workflows、skill gap、FastAPI、PostgreSQL + pgvector | §2.4、FR-24~30、FR-47~49、§9.1 | Phase 0~7 |
| LLM API：prompt engineering、structured outputs、function calling、retry、model fallback、malformed rate（目標 <1%，量測報告） | FR-50~59 | Phase 2 + 2R、7、8 |
| RAG eval：Precision@K、MRR、LLM-as-judge hallucination detection（LangSmith）、vs keyword baseline 改善 %（量測報告） | ER-1~8、FR-60 | Phase 8 |
| LangGraph agent：7 tools 動態選擇 + match-score routing、一次產出三類 artifacts | FR-31~44、FR-47~49、FR-57 | Phase 7 |
| Docker + GitHub Actions CI/CD、coverage ≥80% 工作目標、one-command 部署 | NFR-12~16、TR-1~4 | Phase 0、9、10 |

## Repo 結構（目標結構；`cd.yml`、`docker-compose.prod.yml`、`eval/langsmith/`、`docs/` 報告類為 Phase 8/10 交付）

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
│   │   │   ├── llm/
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
│   ├── datasets/           # 25+ curated profiles/jobs（含 ground-truth 標註）
│   ├── metrics/            # precision@k, mrr, rubric scorer
│   ├── baselines/          # keyword-only matcher etc.
│   ├── langsmith/          # dataset sync + evaluators
│   └── run_eval.py
├── docs/
│   ├── SRS.md
│   ├── architecture.md
│   ├── api.md
│   └── eval_report.md
├── .github/workflows/
│   ├── ci.yml
│   └── cd.yml
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
└── README.md
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

1. DB models（SQLAlchemy）：Phase 1 實際完成 `User`、`RefreshToken`；`Resume`/`ResumeVersion` 於 Phase 3、`Job`/`JobChunk`/`JobEmbedding`（含 `vector(768)` 欄）於 Phase 4 建立；`MatchResult`（Phase 5）、`SkillGapReport`（Phase 6）、`GeneratedArtifact`（Phase 7）由各自 phase 建 model + migration。全部帶 `user_id` FK 並建 index。
2. Alembic migrations 隨 phase 演進（0001 enable pgvector、0002 auth tables、0003 llm_call_log、0004 resume tables、0005 job tables 含 HNSW index；0006 起由後續 phase 接續）
3. Auth：
   - `POST /auth/register`、`POST /auth/login`（JWT access+refresh）、`POST /auth/logout`（blacklist）、`GET /auth/me`
   - bcrypt 雜湊、`get_current_user` 依賴注入
   - Protected routes 以 `Depends(get_current_user)` 依賴注入實作（`app/api/deps.py`）；新增 router（matches / skill-gap）時須逐一掛上
4. 前端：
   - Register / Login 頁、AuthContext、axios interceptor（attach token、401 refresh）
   - Protected route wrapper
5. Tests（目標 6+）：register、login happy/fail、me、protected route 401、user isolation（A 使用者看不到 B 的資料）、refresh token flow

**驗收**：兩個 browser profile 分別註冊登入，看到各自獨立空 dashboard。

---

## Phase 2 — Gemini Wrapper + Structured Output 基建（Week 3 前半, 約 2-3 天）

**對應**：FR-50~56、6.4

1. `app/ai/llm/base.py`：`LLMProvider` abstract（`generate`, `generate_structured(schema)`, `embed`）
2. `app/ai/llm/gemini.py`：封裝 Gemini API，支援：
   - JSON mode（`response_mime_type="application/json"` + `response_schema`）
   - Retry（tenacity，指數退避）
   - Token / latency / cost 紀錄（寫入 dedicated `LLMCallLog`，經 `services/llm_call_log_service.record_call`）
   - Timeout、streaming 預留
3. `GeminiProvider.embed`（`app/ai/llm/gemini.py`）：`gemini-embedding-001`（768 維，MRL 縮維 + L2 正規化），batch 呼叫（`app/ai/embeddings/` 為空 placeholder）
4. Structured output 測試：定義一個 toy schema（e.g. `{name, age}`），驗證 schema validation + retry + fallback parser（修破 JSON）
5. `LLMCallLog` table：provider、model、prompt_hash、tokens_in/out、latency_ms、cost_estimate、status

**驗收**：單元測試呼叫 Gemini、回傳符合 schema 物件；故意回亂字串觸發 retry + fallback 成功。

### Phase 2R — Retrofit（回補已完成部分，於 Phase 5 開始前執行）

**對應**：FR-58~60

1. **Model fallback chain**：`GeminiProvider` 增加 `fallback_model` 參數（`build_gemini_provider` 讀 `settings.gemini_fallback_model`，default `gemini-3.6-flash`）；`generate` / `generate_structured` 外層包 fallback 迴圈——primary 完整流程任何失敗（廣義觸發：含 network retry 耗盡、validation+repair 皆敗、安全阻擋、空回應、model 名錯誤）→ 以 fallback model 完整重跑一次；兩個模型都失敗才丟 `LLMError` / `StructuredOutputError`（例外物件帶記帳 metadata）。`embed` 不設 fallback。
2. **LLMCallLog 擴充**（migration 0006）：追加 `attempts`（int，default 1，含 fallback 的總生成次數）、`repair_used`（bool）、`fallback_used`（bool）；`model` 欄記實際成功（或最後嘗試）之模型；token/成本跨所有嘗試加總、成本按各次實際模型單價分價計算（`record_call` 增 `cost` 參數承接，簽名同步更新）。
3. **LangSmith tracing**：新增 `langsmith` 依賴；`generate` / `generate_structured` / `embed` 加 `@traceable`（未設 key 時 no-op）；`config.py` 增 `gemini_fallback_model`、`langsmith_tracing`、`langsmith_api_key`、`langsmith_project`。
4. **`.env.example`** 追加：`GEMINI_FALLBACK_MODEL`、`LANGSMITH_TRACING`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`；順手補漏的 `EMBEDDING_DIM`。
5. **`pricing.py`** 補 fallback 模型價目（現行 `gemini-3.6-flash`；歷史條目保留）。
6. Tests（2+）：mock primary 永遠回壞 JSON → 驗證 fallback 被呼叫且 `fallback_used=True`；驗證新欄位正確寫入。
7. **Prompt 模板集中**：各任務 system prompts 集中至 `app/ai/prompts/`，parsers 與後續生成服務統一引用，作為 prompt engineering 的明確交付物。

**驗收**：primary 連續失敗時 fallback 至 `gemini-3.6-flash` 成功並於 `LLMCallLog` 留下 `fallback_used=True` 記錄；設定 `LANGSMITH_TRACING=true` 後可在 LangSmith 看到 wrapper trace。

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
5. pgvector 索引（HNSW cosine）
6. API：`GET /jobs`、`GET /jobs/{id}`、`DELETE /jobs/{id}`
7. 前端 Job Management 頁：貼文字、列表、詳細頁
8. Tests（2+）：job 解析、向量寫入與相似度查詢

**驗收**：新增 3 個 job → DB 有 chunks + embeddings → 手動 SQL 查 cosine 相似度正確。

---

## Phase 5 — Match Ranking + Explanation（Week 4 後半, 約 3 天）

**對應**：FR-19~23（含 ResumeEmbedding 前置）

0. **Resume embeddings（前置）**：新 model `ResumeEmbedding`（`vector(768)`，L2-normalized，`kind` 欄位：summary / skills / experience，FK 至 resume version）+ migration；於履歷解析或編輯完成時為 summary、skills、經歷 section 產生 embeddings（查詢側 `task_type="RETRIEVAL_QUERY"`）。目前履歷從未向量化，此為 embedding similarity 的硬前置。
1. Match service 演算法（hybrid）：
   - Embedding similarity（resume summary/skills 向量 vs job chunks 平均/最大；由第 0 步的 ResumeEmbedding 提供，非即時計算）
   - Required skill coverage（normalized string match + Gemini semantic equivalence fallback）
   - Preferred skill coverage
   - Experience alignment（年資、title 語意比對）
   - 加權合成 `match_score`
2. `MatchResult` model + migration；`POST /matches/run`（body: resume_id, job_ids[]）→ 批次計算存 `MatchResult`
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
3. Generation：`SkillGapReport` model + migration；傳 retrieved chunks + resume structured → Gemini structured output 生 `SkillGapReport`：
   - gaps: [{skill, severity (high/med/low), evidence_chunk_ids, suggestion}]
4. Source attribution：回傳 chunk_id → 前端 hover 顯示原文
5. `POST /jobs/{id}/skill-gap`、`GET /skill-gaps/{id}`
6. 前端：Job Detail 頁「Skill Gap Analysis」tab，citation 可展開
7. Tests（2+）：retrieval top-k、citation 完整性

**驗收**：對一個 job 跑 skill gap → 每條 gap 都能點到 source chunk。

---

## Phase 7 — Agent Workflow: Application Kit（Week 5 後半 + Week 6 前半, 約 5-6 天）

**對應**：FR-31~44、FR-45~46、FR-47~49、FR-57（7 tools + LLM 動態選擇 + conditional logic）

1. 依賴：pyproject 增 `langchain-google-genai>=2.0`、`langsmith`；`langgraph` floor 升至 `>=0.2`（註：未來可將 wrapper 遷移至 `google-genai` SDK，非本次範圍）
2. LangGraph agent 設計（hybrid：LLM tool-calling + score routing，對應 FR-57）：
   - **State**：`{resume, job, match_result, skill_gaps, tailored_resume, cover_letter, interview_prep, artifacts}`
   - **Tools（恰好 7 個，名稱與 SRS FR-48 一致，以 `@tool` 包裝、Pydantic args schema）**：
     1. `fetch_resume` — DB 查履歷
     2. `retrieve_job_evidence` — RAG 檢索
     3. `compute_match` — 呼叫 match service
     4. `generate_tailored_resume` — Gemini structured
     5. `generate_cover_letter` — Gemini structured（intro/body/closing）
     6. `generate_interview_qs` — Gemini structured
     7. `save_artifact` — 寫 `GeneratedArtifact`
   - **Planner node（function calling）**：`ChatGoogleGenerativeAI(model=settings.gemini_model).bind_tools([7 tools])`，ReAct 迴圈（planner ↔ `ToolNode`）；system prompt 陳述目標（一次 run 產出並保存三類 artifacts）。工具選擇必須由 LLM 的 function calls 動態決定，graph 不得把 7 個工具寫成固定順序（FR-57）
   - **Conditional edge `route_on_match_score`**（`compute_match` 的 ToolMessage 更新 state 後觸發）：
     - `match_score < 0.5` → 注入 directive：先 `retrieve_job_evidence` + gap 分析，再 tailor resume
     - `match_score >= 0.8` → 注入 directive：跳過 heavy gap analysis，直接生成 tailored resume + cover letter + interview prep
     - 任何 tool failure → 走 fallback node 記錄並 degrade（不整個 crash）
   - **完成檢查 node**：planner 提前結束但三類 artifact 未齊 → bounded re-prompt（`recursion_limit` + timeout 防無限迴圈）
   - **LangSmith**：`LANGSMITH_TRACING=true` 時 LangGraph 全自動 trace，與 wrapper 的 `@traceable` 巢狀呈現
3. Endpoint：`POST /jobs/{id}/generate-application-kit` → 跑 agent → 回 artifacts
4. `GeneratedArtifact` model + migration；artifact 儲存與版本控（FR-45~46：歷史與匯出）
5. 前端 Application Kit 頁：三區塊（resume suggestions / cover letter / interview prep），均可編輯、匯出（copy / .md）
6. Tests（3+）：以 fake ChatModel 注入預錄 `tool_calls` 序列測 (a) 完整流程、(b) 兩個 score 分支、(c) tool failure degrade、(d) LLM 亂選工具時 recursion limit 保護

**驗收**：一次 click 產出三類 artifact；LangSmith trace 可見 LLM 的 function-call 工具選擇與 score 分岔。

---

## Phase 8 — Evaluation Layer（Week 7, 約 5 天）

**對應**：ER-1~9、NFR-1、NFR-5、FR-59

1. **Curated dataset**（`eval/datasets/`）：手刻 25 組 `{resume, jobs[], ground_truth_ranking, ground_truth_gaps, ground_truth_relevant_chunks}`（chunk 級標註僅針對 skill-gap queries），JSON/YAML；`eval/langsmith/sync_datasets.py` 同步至 LangSmith datasets（本地 JSON 為 source of truth）。dataset 設計說明須記錄 ground truth 含「語意匹配但關鍵字不重疊」案例的理由
2. **Matching eval**：hybrid matcher 之 `Precision@K`（K=3,5）與 `MRR` vs **TF-IDF keyword-only baseline**（sklearn）；報告改善百分比（resume 參考目標 35%，measure-and-report）
3. **RAG retrieval eval**：retrieval `Precision@K` / `MRR` vs ground-truth relevant chunks；rerank 前後對比
4. **Hallucination detection（LLM-as-a-judge）**：`langsmith.evaluate()` + custom evaluator，judge = `gemini-3.6-flash`，逐 claim 判 supported / partially / unsupported vs cited chunk 原文；報告 hallucination rate；與「無 RAG 直接生成」baseline 對比。eval_report 須註明 judge 評同家族 flash-lite 產物的 self-grading caveat
5. **Rubric scorer**：resume suggestion 品質（LLM-as-judge + 固定 rubric：relevance / specificity / actionability / alignment；1-5 分）
6. **Malformed-response rate**：script 查 `LLMCallLog` → raw rate、final rate（參考目標 <1%，measure-and-report）、fallback 觸發率
7. **System metrics**：p50/p95 latency（resume parse / job index / match / kit gen）、error rate、backend test coverage（pytest-cov 實測值）
8. `eval/run_eval.py`：一鍵跑全套、輸出 `docs/eval_report.md`（含表格 + baseline 對比）；無 `LANGSMITH_API_KEY` 時本地 metrics（P@K / MRR / malformed rate 等）照算，judge 類跳過並於 report 註明
9. CI 加 nightly eval job（可選）

**驗收**：`python eval/run_eval.py` 產出 report，含全部六類 metrics 與 baseline 對比表；LangSmith 專案可見 evaluate runs。

---

## Phase 9 — 測試補齊 + 硬化（Week 8 前半, 約 3 天）

1. 清點測試數到 **≥15**（auth / api / data / ai pipeline 各領域都覆蓋）；後端 coverage 工作目標 **≥80%**（`pytest --cov=app`），達標後於 CI 加 `--cov-fail-under=80`（達標前僅報告不擋）
2. Integration test：完整 e2e flow（register → upload resume → add job → match → kit）用 `httpx.AsyncClient` + test DB
3. 錯誤處理：LLM timeout、embedding fail、DB down → graceful degradation + 可讀錯誤
4. Rate limiting（slowapi）、input size limit、file type whitelist
5. Secret scan（gitleaks）加入 CI
6. Logging：structured JSON log、request id
7. Frontend：loading / error states、long task progress、表單驗證

**驗收**：`pytest -q` 綠、backend coverage ≥80%（工作目標，實測值寫入 eval_report）、刻意斷網測試不 crash。

---

## Phase 10 — Docker 打包 + 文件 + 交付（Week 8 後半 ~ Week 9, 約 3-5 天）

**對應**：NFR-14~16、Acceptance §10

1. Production Dockerfiles（multi-stage、non-root user、slim base）；backend 啟動時跑 `alembic upgrade head`
2. `docker-compose.prod.yml`：frontend nginx serve build **並反向代理 `/api` → backend**（避免 `VITE_API_URL` 烘進 image）、backend gunicorn+uvicorn workers、db volume；images 直接引用 GHCR
3. **CD**：`.github/workflows/cd.yml` — trigger `push: branches [main], tags ['v*']`；`permissions: packages: write`；`docker/login-action`（`GITHUB_TOKEN`）+ `docker/metadata-action`（tags：sha、latest on main、semver on tag）+ `docker/build-push-action` 推 `ghcr.io/<owner>/careerpilot-backend|frontend`；job 依賴 CI tests 通過
4. `.env.example` 完整 + README quick-start（`cp .env.example .env` → 填 `GEMINI_API_KEY` → `docker compose up`）；部署一鍵指令：`docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`
5. `docs/architecture.md`：高階架構圖 + 資料流
6. `docs/api.md`：OpenAPI 自動生 + 人寫說明
7. `docs/eval_report.md`：metrics + baseline + screenshots
8. Demo 素材：2-3 條示範履歷 + 5 條 job seeds（`scripts/seed.py`）
9. README 最終版：features、screenshots、setup、test、eval 指令
10. Tag `v1.0.0`

**驗收**：乾淨環境 `git clone` + `docker compose up` + seed → 完整 demo flow 可走完；push tag `v1.0.0` → GHCR 出現 backend / frontend 兩個 images → 乾淨機器 `pull && up` 走完 demo flow。

---

## 驗收清單（對照 SRS §10）

- [ ] 註冊/登入/隔離（Phase 1）
- [ ] 履歷上傳 + 可編輯 structured parsing（Phase 3）
- [ ] Job 匯入 + 索引（Phase 4）
- [ ] Match ranking + explanation（Phase 5）
- [ ] Skill gap + source attribution（Phase 6）
- [ ] Resume suggestions / cover letter / interview prep（Phase 7）
- [ ] ≥3 AI techniques（RAG / agent / structured output / embeddings / function calling ✓ 全中）
- [ ] ≥15 tests + CI（Phase 9 + 全程累積）
- [ ] Docker 重現（Phase 10）
- [ ] ≥2 quantitative metrics + baseline（Phase 8）
- [ ] LLM function-calling 7-tool agent + match-score routing（Phase 7）
- [ ] Model fallback chain + malformed rate 報告（Phase 2R + 8）
- [ ] LangSmith eval：matching / RAG P@K・MRR + hallucination rate + rubric（Phase 8）
- [ ] Backend coverage ≥80% 工作目標（Phase 9）
- [ ] GHCR CD：main / `v*` tag 自動發佈 images（Phase 10）

## 風險與緩解

| Risk | Mitigation |
|---|---|
| Gemini structured output 不穩 | schema validation + retry + fallback JSON repair（Phase 2 內建）+ `gemini-3.6-flash` model fallback（Phase 2R） |
| pgvector index 小資料集效果差 | HNSW cosine index（Phase 4 已建）；資料集小時必要可退回 exact search |
| Agent 無限迴圈 | LangGraph recursion_limit + timeout |
| Token 成本爆炸 | Phase 2 token log、長文先摘要、cache resume 向量 |
| fallback 模型（3.6-flash）成本與 20 RPD 額度 | 僅 primary retry 耗盡後觸發、LLMCallLog 追蹤 `fallback_used` 比率 |
| LangSmith 不可用 | tracing 未設 env 即停用、judge 類 eval 可跳過、本地 metrics 照算 |
| chunk 級 ground-truth 標註耗時 | 只標 skill-gap queries、標註 guideline 先行 |
| Eval dataset 太花時間 | Week 1 就開始零散蒐集 real job posts、Week 7 統一整理 |

## 每週節奏（建議）

| Week | 焦點 |
|---|---|
| 1 | Phase 0 |
| 2 | Phase 1 |
| 3 | Phase 2 + 3 |
| 4 | Phase 2R + 4 + 5 |
| 5 | Phase 6 + 7a |
| 6 | Phase 7b |
| 7 | Phase 8 |
| 8 | Phase 9 + 10a |
| 9 | Phase 10b + buffer |
| 10 | Demo 錄製 / 報告撰寫 / 最終修復 |

## 驗證（交付前手動 end-to-end 測試）

1. `docker compose down -v && docker compose up --build` — 全新環境可跑
2. 註冊 user A → 上傳 sample PDF → 結構化解析正確
3. 新增 3 個 jobs → match → ranking 合理 → explanation 有意義
4. 對 top job 跑 application kit → 三類 artifact 產出 + citation 可點
5. 登出 → 登入 user B → 完全看不到 A 的資料
6. `pytest -q` 全綠、`npm test` 全綠
7. `python eval/run_eval.py` → 產出 report，含六類 metrics、vs baseline 改善 % 與 malformed rate
8. Push → GitHub Actions CI 綠燈
9. Push tag `v*` → cd.yml 綠燈 → GHCR images 可 `docker pull`
