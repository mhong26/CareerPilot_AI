# Phase 7 實作規劃書 — Agent Workflow: Application Kit

> 對應：plan.md Phase 7、SRS FR-31~44、FR-54~55、FR-56~58、FR-65~66、NFR-4
> 撰寫日期：2026-07-30。本文件依據對現有程式碼的完整盤點撰寫，所有「重用」項目均已確認存在。

## 0. 已拍板的決策

| 決策點 | 結論 |
|---|---|
| Endpoint 執行模式 | **同步執行**：`POST` 請求內跑完 agent（預估 30~90 秒），前端顯示 loading。與現有 skill-gap 端點同模式，長任務進度優化留給 Phase 10。 |
| 履歷來源 | **可選 `resume_id`，預設 current**：request body 可帶 `resume_id`，未帶則用 `resume_service.get_current_resume()`。 |
| 前端形式 | **獨立路由頁** `/jobs/:jobId/application-kit` + `ApplicationKitPage`，Job Detail 頁放入口按鈕。 |
| 規劃書形式 | 存為本文件（`docs/phase7_plan.md`）。 |

依 repo 既有慣例由規劃者決定（理由見各步驟）：前端沿用「async 函式 + `useState`」模式（不啟用 TanStack Query hooks）；graph 的工具執行採**自訂 tool executor node**（非 langgraph prebuilt `ToolNode`）；`save_artifact` 每個 artifact 單獨 commit；planner 的 LLM 呼叫也記入 `LLMCallLog`。

---

## 1. 目標與範圍

**一句話**：使用者在職缺頁按一個按鈕，由一個 LangGraph agent（LLM 以 function calling 在 7 個工具間動態決策）一次產出並保存三類 artifacts——tailored resume suggestions、cover letter、interview prep——前端可檢視、編輯、匯出。

### 1.1 需求對照

| 需求 | 本 phase 如何滿足 |
|---|---|
| FR-31~36 客製化履歷建議 | `generate_tailored_resume` 工具 + `TailoredResumeSuggestions` schema（section 級、bullet rewrite、關鍵字、理由） |
| FR-37~40 Cover letter | `generate_cover_letter` 工具 + `CoverLetterDraft` schema（intro / body / closing）+ 前端可編輯保存 |
| FR-41~44 面試準備題 | `generate_interview_qs` 工具 + `InterviewPrepSet` schema（四類題型、含 answer outline） |
| FR-54~55 歷史與匯出 | `GeneratedArtifact` append-only 版本控 + 前端 copy / .md 下載 |
| FR-56 multi-step agent | LangGraph graph，單次 run 產出並保存三類 artifacts |
| FR-57 恰好 7 個工具 | `fetch_resume`、`retrieve_job_evidence`、`compute_match`、`generate_tailored_resume`、`generate_cover_letter`、`generate_interview_qs`、`save_artifact` |
| FR-58 條件分流 | `route_on_match_score`：score < 0.5 / ≥ 0.8 注入不同 directive |
| FR-66 LLM 動態選工具 | planner = `ChatGoogleGenerativeAI.bind_tools(7 tools)`，graph 不硬寫順序 |
| FR-65 token/成本紀錄 | 工具內生成走既有 wrapper + `record_call`；planner 呼叫另記 `operation="agent_planner"` |
| NFR-4 graceful degradation | 工具失敗 → executor 降級不 crash；三類不齊 → 回傳 partial + errors |

### 1.2 交付物清單

- 後端：`GeneratedArtifact` model + migration `0009`、`app/ai/parsers/kit_schema.py`、`app/ai/prompts/kit.py`、`app/ai/agents/`（context / tools / state / graph 四模組）、`app/services/application_kit_service.py`、`app/schemas/application_kit.py`、`app/api/application_kits.py`（3 個端點）
- 前端：`ApplicationKitPage` + 路由、`src/api/applicationKit.ts`、`src/types/applicationKit.ts`、JobDetail 入口按鈕、copy / .md 匯出
- 測試：後端約 12+ 新測試（含 scripted planner 四情境）、前端 2~3 個
- 依賴：`langchain-google-genai>=2.0`、`langgraph` floor 升 `>=0.2`

---

## 2. 背景知識：這個 phase 的核心概念

> 已有背景者可跳到 §3。

### 2.1 Function calling：LLM「呼叫工具」的真相

LLM 只會輸出文字，不能執行程式。所謂 function calling 是一個四步協議：

1. **給說明書**：把每個工具的名稱、用途、參數格式（JSON Schema）隨每次 API 請求附給模型——LangChain 的 `llm.bind_tools([...])` 就是做這件事。
2. **模型回「呼叫請求」而非文字**：例如 `{"name": "compute_match", "args": {}}`。Gemini 原生支援這種輸出（跟 Phase 2 的 structured output 同源技術）。
3. **我們的程式真正執行**：框架依名稱找到對應 Python 函式、帶入參數執行。
4. **結果包成 `ToolMessage` 塞回對話**，再問模型「下一步？」。

重複 2~4 直到模型不再發出工具呼叫。這個「思考 → 選工具 → 看結果 → 再思考」的循環叫 **ReAct 迴圈**。整個 agent 本質上就是一個 while 迴圈。

### 2.2 LangGraph：把迴圈畫成狀態圖

手寫 while 迴圈要自己處理狀態傳遞、防無限迴圈、條件分岔、錯誤降級。LangGraph 把這些框架化：

- **State**：一個共享資料容器（TypedDict），所有節點讀寫它，可想成「任務進度表」。
- **Node**：一個工作站（planner、工具執行、完成檢查……），輸入 state、回傳 state 的更新。
- **Edge**：節點間的固定路線；**conditional edge** 則在執行時看 state 決定走哪條。
- **`recursion_limit`**：整張圖最多走幾步的保險絲，防 LLM 鬼打牆。

### 2.3 為什麼不是「呼叫三次 LLM」就好？

因為流程應該視情況而變：履歷與職缺很匹配時不必做重度缺口分析；很不匹配時該先檢索證據再寫建議。FR-66 明文要求工具選擇由 LLM 動態決定、**graph 不得把 7 個工具寫成固定順序**——只允許以 match score 做 routing（FR-58）與安全防護（step limit、timeout）約束。

---

## 3. 現況盤點：可重用的元件與必須遵守的慣例

偵察已確認以下元件存在且可直接重用：

| 既有元件 | 位置 | Phase 7 用途 |
|---|---|---|
| `LLMProvider` / `GeminiProvider`（sync；retry + repair + model fallback） | `app/ai/llm/base.py`、`gemini.py` | 三個 generate 工具的 structured output |
| `record_call(db, *, ...)`（自帶 commit） | `app/services/llm_call_log_service.py` | 所有 LLM 呼叫記帳（含 planner） |
| `run_matches(db, *, user, resume_id, job_ids, provider)` → `(ResumeVersion, [(MatchResult, Job)], skipped)` | `app/services/match_service.py` | `compute_match` 工具（docstring 已預告 Phase 7 直接呼叫） |
| `MatchResult.match_score`（top-level Float，註解明言 0.5/0.8 routing 用） | `app/db/models/match.py` | score routing |
| `retrieve_job_chunks(db, *, job_id, query_vector, top_k=5)` → `list[RetrievedChunk]` | `app/ai/rag/retrieval.py` | `retrieve_job_evidence` 工具（docstring 已預告重用） |
| `Reranker` Protocol + `CrossEncoderReranker` + `rerank_order` | `app/ai/rag/rerank.py` | 檢索後 rerank（失敗降級向量序） |
| `get_current_resume` / `get_resume` / `get_current_version` / `get_version_embeddings` / `generate_resume_embeddings` | `app/services/resume_service.py` | `fetch_resume` 工具 + 檢索 query 向量 |
| `get_job` + parse/index gates | `app/services/job_service.py` | 前置檢查 |
| skill-gap router（無 prefix、混合路徑、409/404 映射、`_to_response`） | `app/api/skill_gaps.py` | 新 router 的直接範本 |
| `@traceable` + config.py 匯入時回填 `LANGSMITH_*` env | `gemini.py`、`core/config.py` | LangSmith 巢狀 trace 的基礎 |
| `_FakeProvider` + `app.dependency_overrides` 測試模式 | `tests/test_skill_gaps.py` 等 | 工具層生成的測試注入 |

**必須遵守的慣例**（違反會與全 repo 不一致）：

1. **全部 sync**：整個 backend 零 `async def`；LangGraph 用同步 `.invoke()`。
2. `record_call` **自帶 commit**；LLM 記帳與業務寫入的交易關係要想清楚（見 Step 6）。
3. LLM 生成失敗的慣例是**回 `(None, error_str)` 不丟例外**，HTTP 照樣 200，錯誤字串持久化（NFR-4）。
4. Prompt 模組：`<TASK>_SYSTEM` 常數 + 純函式 `build_<task>_prompt(*, ...)` + `_MAX_*` 截斷常數；`__init__.py` 全空、import 用完整路徑。
5. Structured output schema：**每個欄位都有 default**（部分輸出仍可通過驗證）、`Field(description=...)` 引導 Gemini、citation 用 1-based int。
6. Model：`class X(UUIDPKMixin, TimestampMixin, Base)`、`user_id` FK CASCADE + index、子表冗餘 `user_id`、狀態欄用 `String(16/32)` 加註解（不用 Enum）、新 model 必須在 `app/db/models/__init__.py` 註冊。
7. Migration：手寫 style、revision `"0009"`、down_revision `"0008"`。
8. 測試：真 Postgres + `TRUNCATE` 清理——**新表必須加進 conftest 的 TRUNCATE 清單**，否則測試間狀態外洩。
9. 狀態碼：compute-and-upsert 動作回 200（非 201）；前置條件不滿足 409；他人資源一律 404（不可探測）。
10. 前端：`src/api/*.ts` 具名 async 函式（404 吞掉回 `null`）、頁面 `useEffect + useState` 自管狀態、Tailwind 慣用字串、`src/types/*.ts` 鏡像後端 schema 保留 snake_case。

---

## 4. 整體架構

### 4.1 一次請求的流程

```
POST /jobs/{job_id}/generate-application-kit  {resume_id?}
  │
  ├─ gates（服務層，agent 啟動前）：
  │    resume 存在且 parse_status=="parsed" 且有 version → 否則 404/409
  │    job 存在且 parsed_data 非空且 index_status=="indexed" → 否則 404/409
  │
  ├─ 建 KitRunContext（db、user、resume、version、job、provider、reranker、deadline）
  ├─ build_kit_tools(ctx) → 7 個工具（closure 綁定 ctx）
  ├─ build_kit_graph(planner_model, tools) → 編譯 graph
  ├─ graph.invoke(初始 state, config={"recursion_limit": 50})
  │
  └─ 組裝 ApplicationKitResponse（三類 artifacts + missing + errors + match_score）→ 200
```

### 4.2 Graph 形狀

```
            START
              │
              ▼
        ┌───────────┐  有 tool_calls   ┌───────────────┐
        │  planner  │ ───────────────▶ │ tool executor  │──── 每個呼叫逐一執行，
        │ (Gemini + │                  │ (自訂 node)    │     失敗→錯誤 ToolMessage
        │ bind_tools)│ ◀──┐            └───────┬───────┘     （degrade，不 crash）
        └─────┬─────┘    │                    │
              │          │   ┌────────────────┴───────────────┐
   沒有 tool_calls        │   │ compute_match 剛完成且未發過指令？ │
              │          │   └────────────────┬───────────────┘
              ▼          │              是 ▼           否 → 回 planner
      ┌──────────────┐   │      ┌──────────────────────┐
      │ completion   │   └──────│ route_on_match_score │
      │ check        │          │ (注入 directive 訊息) │
      └──────┬───────┘          └──────────────────────┘
   三類已存？ │
     是 → END │ 否且 reprompt < 2 → 注入提醒 → planner
             │ 否且 reprompt 已滿 → END（partial）
```

---

## 5. 實作步驟

> 建議依序執行；Step 1~5 彼此獨立可先行測通，Step 6~8 是核心，Step 9~11 收尾。

---

### Step 1 — 依賴與環境（約 0.5 天）

**原理**：planner 需要一個「會講 LangChain 語言」的 Gemini 聊天模型。我們既有的 `GeminiProvider` 是自家抽象、沒有 `bind_tools` 介面；為 planner 引入官方整合套件 `langchain-google-genai`（提供 `ChatGoogleGenerativeAI`，原生支援 Gemini function calling），而三個 generate 工具**內部仍走既有 wrapper**（保留 retry / repair / fallback / 記帳），兩者並存、各司其職。

**做法**：

1. `backend/pyproject.toml` dependencies 追加 `langchain-google-genai>=2.0`，`langgraph>=0.1.0` 升為 `langgraph>=0.2`。
2. **不動** Dockerfile 的 torch 兩步安裝（torch 必須先從 PyTorch CPU index 單獨裝——該 index 藏有 2022 年版 requests/urllib3，混用會被 uv 的 first-index 策略吸進來，phase6_notes 有記載）。
3. **相容性驗證**（重要風險）：repo 目前用**舊版 SDK** `google-generativeai>=0.7.0`（`import google.generativeai`），而 `langchain-google-genai` 依賴新的 Google SDK 家族，且 repo **沒有 lock file**（Docker/CI 每次從 PyPI 現解）。裝完後必跑：
   - `uv pip install --system -r pyproject.toml --extra dev` 後執行 `python -c "import google.generativeai; from langchain_google_genai import ChatGoogleGenerativeAI"` 確認共存；
   - `pytest tests/test_gemini_provider.py` 確認舊 wrapper 不受影響；
   - 若真衝突，備援方案：把 planner 也遷到 `langchain-google-genai` 之外的自製 tool-calling 薄層（成本高，先驗證再說）。
4. LangSmith：config.py 已在匯入時回填 `LANGSMITH_TRACING/API_KEY/PROJECT` 至 `os.environ`；新版 langchain-core 直接讀 `LANGSMITH_*`。Step 11 實測若 LangGraph trace 沒出現，於 config.py 同處補 `os.environ.setdefault("LANGCHAIN_TRACING_V2", ...)` 等別名。

**驗收**：CI 綠燈（依賴解析成功、既有 104 測試全過）、上述 import 冒煙測試通過。

---

### Step 2 — `GeneratedArtifact` model + migration 0009（約 0.5 天）

**原理**：三類 artifacts 要「保存歷史、不得破壞舊版」（FR-54、SRS §5.3.4）。做法是 **append-only**：每次生成或編輯都**插入新 row**，永不 UPDATE 內容、永不 DELETE。「最新版」= 同 `(resume_id, job_id, kind)` 下 `version_number` 最大者。這與 `MatchResult`/`SkillGapReport` 的「每對唯一、覆寫升級」不同——那兩者是「分析快照」，artifacts 是「創作產物」，歷史本身有價值。

**做法**：

1. 新檔 `app/db/models/generated_artifact.py`：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `created_at` / `updated_at` | mixin | `UUIDPKMixin, TimestampMixin` |
| `user_id` | UUID FK `users.id` CASCADE, index | 擁有者（隔離查詢用） |
| `resume_id` | UUID FK `resumes.id` CASCADE | 來源履歷 |
| `resume_version_id` | UUID FK `resume_versions.id` CASCADE, index | 生成當下的履歷版本快照 |
| `job_id` | UUID FK `jobs.id` CASCADE, index | 目標職缺 |
| `run_id` | UUID, index | 同一次 agent run 的三個 artifacts 共用（服務層產生）；編輯版沿用原 run_id |
| `kind` | String(32) | `"tailored_resume"` / `"cover_letter"` / `"interview_prep"`（註解記載，不用 Enum——repo 慣例） |
| `source` | String(16) | `"agent"`（agent 產出）/ `"edit"`（使用者編輯版） |
| `version_number` | Integer | 同 `(resume_id, job_id, kind)` 遞增，1 起算 |
| `content` | JSONB NOT NULL | 對應 kind 的 structured payload（Step 3 schema 的 `model_dump()`） |

   - `__table_args__`：`Index("ix_generated_artifacts_pair_kind", "resume_id", "job_id", "kind")`（查最新版用）。不設 unique constraint（append-only 允許多 row）。
   - 不宣告 `relationship()`（result/ledger 類表的既有慣例）。
2. `app/db/models/__init__.py` 註冊 `GeneratedArtifact`（alembic env 與測試 conftest 靠它看見 metadata）。
3. 新 migration `backend/alembic/versions/0009_generated_artifacts.py`：手寫 style，revision `"0009"`、down_revision `"0008"`，`create_table` 完整列出 id/created_at/updated_at（`server_default gen_random_uuid()/now()`）、FK 帶 ondelete、index 用 `op.f()` 命名。
4. **`tests/conftest.py` 的 TRUNCATE 清單加入 `generated_artifacts`**（漏了會讓測試互相污染——盤點時特別標記的陷阱）。

**驗收**：`alembic upgrade head` 成功；`alembic downgrade -1` 再 upgrade 可逆；跑既有測試確認 create_all 無衝突。

---

### Step 3 — 三個 structured output schemas（約 0.5 天）

**原理**：三類 artifacts 都必須是可驗證 schema 的 structured output（FR-59），這樣才能存 JSONB、在前端分區塊渲染與編輯、被 Phase 9 的 rubric evaluator 逐欄位評分。遵守既有慣例：每欄位都有 default（LLM 輸出不完整也能過驗證）、`Field(description=...)` 引導模型。

**做法**：新檔 `app/ai/parsers/kit_schema.py`（parsers/ 是 structured-output schema 的既有家）：

```python
# TailoredResumeSuggestions（FR-31~35）
class BulletRewrite(BaseModel):
    original: str = ""          # 原句（可空，代表建議新增）
    improved: str = ""          # 可直接採用的改寫（FR-33）
    reason: str = ""            # 為什麼——對齊 job requirement / skill gap（FR-35）

class SectionSuggestion(BaseModel):
    section: str = ""           # "summary" / "experience" / "projects" / "skills"（FR-32）
    bullet_rewrites: list[BulletRewrite] = Field(default_factory=list)
    keywords_to_add: list[str] = Field(default_factory=list)   # FR-34
    note: str = ""

class TailoredResumeSuggestions(BaseModel):
    overall_strategy: str = ""
    section_suggestions: list[SectionSuggestion] = Field(default_factory=list)
    top_keywords: list[str] = Field(default_factory=list)

# CoverLetterDraft（FR-39：intro / body / closing 結構化區塊）
class CoverLetterDraft(BaseModel):
    intro: str = ""
    body_paragraphs: list[str] = Field(default_factory=list)
    closing: str = ""

# InterviewPrepSet（FR-42~43）
class InterviewQuestion(BaseModel):
    question: str = ""
    category: str = ""          # "technical" / "behavioral" / "project-based" / "skill-gap-focused"
    why_it_matters: str = ""
    related_resume_area: str = ""
    answer_outline: list[str] = Field(default_factory=list)

class InterviewPrepSet(BaseModel):
    questions: list[InterviewQuestion] = Field(default_factory=list)
```

**驗收**：`Model.model_validate({})` 全部通過（default 慣例）；`to_gemini_schema()` 能轉換不報錯（單元測試）。

---

### Step 4 — Prompts（約 0.5 天）

**原理**：prompt 是這個 phase 的「規格書」——planner 的 system prompt 決定 agent 行為邊界；三個 generator prompt 決定產物品質。集中在 `app/ai/prompts/` 是 Phase 2R 定下的 prompt engineering 交付慣例。

**做法**：新檔 `app/ai/prompts/kit.py`，四組內容：

1. `KIT_PLANNER_SYSTEM`：陳述目標與規則——「你是求職申請包助理。目標：在這次 run 內產出**並用 `save_artifact` 保存**全部三類 artifacts（tailored_resume、cover_letter、interview_prep）。可用工具七個；先取得履歷與 match 資訊再生成；工具失敗時繼續完成其餘目標；全部保存完畢即結束。」**注意不寫死順序**（FR-66），只講目標與完成條件。
2. `TAILORED_RESUME_SYSTEM` + `build_tailored_resume_prompt(*, job_title, job_company, resume_parsed_summary, match_missing_skills, evidence_chunks, skill_gap_hints)`：要求每條建議附 reason 且引用 job 證據。
3. `COVER_LETTER_SYSTEM` + `build_cover_letter_prompt(*, ...)`：依履歷亮點 × job 要求 × match/gap 結果客製（FR-38；使用者偏好欄位 Phase 8 才注入，本 phase 留參數缺省）。
4. `INTERVIEW_QS_SYSTEM` + `build_interview_qs_prompt(*, ...)`：四類題型都要覆蓋、gap 相關題引用缺口技能。

沿用 `_MAX_PROMPT_CHARS`、`_MAX_CHUNK_CHARS` 等截斷常數慣例。

**驗收**：純函式單元測試（給定輸入產出含關鍵區塊的 prompt 字串、截斷生效）。

---

### Step 5 — 三個生成函式（約 0.5 天）

**原理**：generate 工具內部不直接呼叫 provider——依 repo 慣例包成「call + log + 降級」函式：計時 → `provider.generate_structured(prompt, Schema, system=...)` → 成功/失敗都 `record_call` 記帳 → 失敗回 `(None, error_str)` 不丟例外。這正是 `skill_gap_service.generate_skill_gap_analysis` 的既有模式，讓 malformed rate（FR-68）與成本追蹤自動涵蓋新呼叫。

**做法**：在 `app/services/application_kit_service.py` 內寫一個私有 helper 消除三份重複：

```python
def _generate_payload(db, *, prompt, system, schema, provider, user_id) -> tuple[BaseModel | None, str | None]:
    # 計時 → generate_structured → record_call(success/error) → (data, None) 或 (None, str(exc))
```

三個薄包裝分別套 `TailoredResumeSuggestions` / `CoverLetterDraft` / `InterviewPrepSet`。`operation` 記 `"generate_structured"`（既有值域）。

**驗收**：以 `_FakeProvider` 注入成功與 `StructuredOutputError` 兩情境的單元測試，確認 `LLMCallLog` 寫入正確。

---

### Step 6 — Agent 工具層：context + 7 tools（約 1 天）

**原理（兩個關鍵設計）**：

- **Closure 工廠**：`@tool` 函式只會收到 LLM 給的參數，但工具實際需要 `db`、`user`、`provider` 等資源——這些**絕不能讓 LLM 控制**（安全：LLM 不該能指定別人的 user_id；正確性：db session 不是文字）。解法：把資源裝進一個 `KitRunContext` 物件，用工廠函式 `build_kit_tools(ctx)` 在內部定義 7 個工具函式（closure 捕獲 ctx），LLM 看到的參數表只剩業務參數。
- **精簡回傳原則**：工具的完整產物（整份履歷、整組建議）寫進 **ctx**（Python 物件，不過 LLM）；回給 LLM 的 `ToolMessage` 只放**精簡摘要**（幾百字內）。原因：LLM 每一輪都要重讀全部對話，把大 JSON 塞回去會燒 token、稀釋注意力，且 planner 只需要「知道做完了、關鍵數字是什麼」就能決策。

**做法**：

1. 新檔 `app/ai/agents/context.py` — `@dataclass class KitRunContext`：`db: Session`、`user: User`、`resume: Resume`、`version: ResumeVersion`、`job: Job`、`provider: LLMProvider`、`reranker: Reranker`、`run_id: uuid.UUID`、`deadline: float`（monotonic 秒）、暫存區 `retrieved_chunks: list[RetrievedChunk]`、`match_result: MatchResult | None`、`payloads: dict[str, BaseModel]`（三類生成暫存）、`saved: dict[str, uuid.UUID]`（kind → artifact id）、`errors: list[str]`。

2. 新檔 `app/ai/agents/tools.py` — `build_kit_tools(ctx) -> list[BaseTool]`，7 個工具（名稱與 SRS FR-57 逐字一致；`@tool` + Pydantic args schema）：

| 工具 | LLM 參數 | 做什麼（讀寫 ctx） | 回給 LLM 的摘要 |
|---|---|---|---|
| `fetch_resume` | 無 | 把 `version.parsed_data` 驗證成 `ResumeParsed` 存 ctx | 姓名、年資、top skills（截斷）、section 概況 |
| `retrieve_job_evidence` | `focus: str = ""`（可選檢索焦點） | 取 `get_version_embeddings`（kind 優先序 skills→summary→experience，缺則 `generate_resume_embeddings` 補）→ `retrieve_job_chunks`（top_k=5）→ `reranker.predict` + `rerank_order`（失敗降級向量序）→ chunks 存 ctx | 各 chunk 的 section + 前 200 字摘要（1-based 編號） |
| `compute_match` | 無 | `match_service.run_matches(db, user=..., resume_id=..., job_ids=[job.id], provider=...)` → `MatchResult` 存 ctx | `match_score`、missing_required、matched_required 摘要 |
| `generate_tailored_resume` | 無 | 用 ctx 素材 build prompt → Step 5 生成函式 → payload 存 `ctx.payloads["tailored_resume"]`；失敗記 `ctx.errors` | 「已生成 N 個 section 建議」或錯誤訊息 |
| `generate_cover_letter` | 無 | 同上 → `ctx.payloads["cover_letter"]` | 「已生成（intro/body×N/closing）」或錯誤 |
| `generate_interview_qs` | 無 | 同上 → `ctx.payloads["interview_prep"]` | 「已生成 N 題（含四類）」或錯誤 |
| `save_artifact` | `kind: Literal["tailored_resume","cover_letter","interview_prep"]` | 讀 `ctx.payloads[kind]`（沒有 → 回錯誤提示「先生成」）；算 `version_number = 該 pair+kind 現有 max + 1`；插入 `GeneratedArtifact`（source="agent"、run_id）並 **commit**；id 記入 `ctx.saved` | 「已保存 kind vN」 |

   - 生成工具的 prompt 素材取用順序：resume parsed（必要，未 fetch 則工具自行 lazy 載入並提示 planner）、`ctx.match_result` 的 missing skills（有才用）、`ctx.retrieved_chunks`（有才用）、同 pair 既有 `SkillGapReport.analysis`（DB 查得到就當 hint——重用 Phase 6 成果，不強制）。
   - **commit 策略**：`save_artifact` 逐 artifact commit。理由：agent 天生交替「LLM 呼叫（`record_call` 自帶 commit）」與「寫入」，skill-gap 式的「單一最終 commit」不變式在此無法維持；每個 artifact 是獨立原子單位，逐筆 commit 讓 run 中途失敗時已保存的 artifacts 仍然有效（NFR-4）。

3. 工具內部**不** try/except 吞錯（生成類已經回 `(None, error)` 是例外）；資源類錯誤讓它拋出，由 Step 7 的 executor 統一捕捉降級——單一職責，測試也好寫。

**驗收**：每個工具的單元測試（直接呼叫 closure，斷言 ctx 讀寫與回傳字串）。

---

### Step 7 — LangGraph graph（約 1.5 天，核心）

**原理**：graph 是「planner 與工具的往返迴圈」加三個客製機制——score routing、完成檢查、降級。工具執行採**自訂 executor node** 而非 prebuilt `ToolNode`，因為我們需要在同一個地方做三件 prebuilt 不便做的事：(a) 工具拋錯時轉成錯誤 `ToolMessage` 降級（fallback 行為，plan.md 的「fallback node」以此實現）；(b) 把 `compute_match` 的分數同步進 graph state 供 conditional edge 使用；(c) 把 `save_artifact` 的完成狀態同步進 state 供完成檢查。

**做法**：

1. 新檔 `app/ai/agents/state.py`：

```python
class KitState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]   # 對話累積（LangGraph 內建 reducer）
    match_score: float | None       # compute_match 後填入
    directive_issued: bool          # score directive 只發一次
    reprompt_count: int             # 完成檢查的重提示次數
    timed_out: bool
```

（三類 artifact 的保存狀態直接讀 `ctx.saved`——DB 為準的事實來源，不複製進 state。）

2. 新檔 `app/ai/agents/graph.py` — `build_kit_graph(planner_model, tools, ctx) -> CompiledGraph`：

   - **planner node**：
     - 先檢查 `time.monotonic() > ctx.deadline`（常數 `KIT_DEADLINE_SECONDS = 240`）→ 超時則設 `timed_out=True` 直接讓流程走向 completion check（不再呼叫 LLM）——這就是 plan.md 要求的 timeout 防護。
     - 否則 `planner_model.bind_tools(tools).invoke(messages)`；計時並從回應的 `usage_metadata` 取 token 數 → `record_call(db, operation="agent_planner", model=settings.gemini_model, ...)`（FR-65 完整覆蓋；`usage_metadata` 缺失時記 `TokenUsage()` 零值）。
     - planner LLM 本身拋錯（網路等）：重試一次，仍失敗 → 記 `ctx.errors`、視同「沒有 tool_calls」讓流程進 completion check（partial 收場，不 crash）。
   - **conditional edge（planner 之後）**：最後一則 `AIMessage` 有 `tool_calls` → `execute_tools`；沒有 → `completion_check`。
   - **execute_tools node（自訂 executor）**：逐一執行 `tool_calls`：
     - 依名稱查工具、`tool.invoke(args)`、結果包 `ToolMessage(tool_call_id=...)`。
     - `except Exception` → 錯誤訊息包成 `ToolMessage`（`"[tool_error] fetch_resume failed: ..."`）、記 `ctx.errors`——planner 看得到失敗、可改變策略，整個 run 不中斷（NFR-4）。
     - 若剛執行過 `compute_match` 且成功 → 把分數寫進 state `match_score`。
   - **conditional edge `route_on_match_score`（execute_tools 之後）**：`match_score` 有值且 `directive_issued` 為 False → 走 **inject_directive node**；否則回 planner。
   - **inject_directive node**：依門檻注入一則 `HumanMessage`（前綴 `[directive]`；不用 mid-conversation SystemMessage，Gemini 的 system 轉換對中途插入不友善）：
     - `< 0.5`：「匹配偏低。先呼叫 `retrieve_job_evidence` 取得職缺證據、確認缺口，再生成 tailored resume，使建議聚焦於補足差距。」
     - `>= 0.8`：「匹配很高。可跳過額外檢索，直接生成三類 artifacts。」
     - 中間帶：只注入「分數中等，自行判斷是否需要更多證據」——保留 LLM 決策空間（FR-66）。
     - 設 `directive_issued=True` → 回 planner。
   - **completion_check node**：查 `ctx.saved` 的三個 kind：
     - 全齊或 `timed_out` → END。
     - 缺且 `reprompt_count < 2` → 注入提醒訊息（「尚未保存：cover_letter。請完成生成並呼叫 save_artifact。」）、`reprompt_count += 1` → 回 planner。
     - 缺且已重提示 2 次 → END（partial；缺漏由 service 寫進 response）。
   - **編譯與保險絲**：`graph.compile()`；invoke 時 `config={"recursion_limit": 50}`；service 層 `except GraphRecursionError` → 收集已保存的 artifacts 組 partial response（測試情境 d）。

3. **FR-66 合規自查**：graph 內沒有任何「工具 A 之後必然工具 B」的硬連線；唯二的流程干預是 score directive（FR-58 允許）與 step/timeout 防護（FR-66 允許）。directive 是「注入建議訊息」而非「強制路徑」，最終選擇權在 LLM。

**驗收**：Step 10 的 scripted planner 四情境測試全過。

---

### Step 8 — Service 編排 + API（約 1 天）

**做法**：

1. `app/services/application_kit_service.py`：
   - 例外類：`KitResumeNotReadyError`、`KitJobNotIndexedError`、`ArtifactNotFoundError`（沿用「服務丟語意例外、router 轉 HTTP」慣例；`ResumeNotFoundError`/`JobNotFoundError` 直接重用既有的）。
   - `run_application_kit(db, *, user, resume_id: uuid.UUID | None, job_id, provider, reranker, planner_model) -> KitRunOutcome`：
     1. 解析履歷：`resume_id` 給定 → `get_resume`（404 語意）；未給 → `get_current_resume`（無 → `KitResumeNotReadyError`）。Gates：`parse_status == "parsed"` 且有 current version、job `parsed_data` 非空且 `index_status == "indexed"`（否則 409 語意例外）——與 skill-gap gates 同款，**agent 啟動前**就擋掉必敗的 run，省 LLM 成本。
     2. 建 ctx（`run_id = uuid4()`、deadline）→ build tools → build graph → invoke（含 recursion_limit 與 `GraphRecursionError` 捕捉）。
     3. 組 `KitRunOutcome`：從 `ctx.saved` 載回三個 `GeneratedArtifact` rows、`match_score`、`missing`（未齊的 kind）、`errors`。
   - `get_latest_kit(db, *, user, resume_id, job_id) -> dict[str, GeneratedArtifact]`：每 kind 取 `version_number` 最大 row。
   - `update_artifact(db, *, user, artifact_id, content: dict) -> GeneratedArtifact`：找原 row（他人/不存在 → 404 語意）→ 依 kind 用對應 schema `model_validate(content)`（schema 驗證，SRS §5.3.3）→ **插入新 row**（同 pair/kind/run_id、`version_number+1`、`source="edit"`）→ commit。append-only，舊版永在（FR-54、§5.3.4）。

2. `app/schemas/application_kit.py`：
   - `GenerateKitRequest {resume_id: uuid.UUID | None = None}`；`ArtifactUpdateRequest {content: dict}`。
   - `ArtifactResponse {id, kind, source, version_number, run_id, resume_version_number, created_at, content: dict}`（content 在 router `_to_response` 時已按 kind 驗證回典型結構——JSONB → typed 的既有慣例）。
   - `ApplicationKitResponse {job_id, resume_id, match_score: float | None, tailored_resume: ArtifactResponse | None, cover_letter: ArtifactResponse | None, interview_prep: ArtifactResponse | None, missing: list[str], errors: list[str]}`。

3. `app/api/application_kits.py` — `APIRouter(tags=["application-kit"])` 無 prefix（混合路徑，skill_gaps 先例）：
   - 注入 seams（測試覆蓋點）：`get_llm_provider()`、`get_reranker()`（照抄 skill_gaps 模式）、**`get_planner_model() -> BaseChatModel`**（回 `ChatGoogleGenerativeAI(model=settings.gemini_model, google_api_key=settings.gemini_api_key, temperature=0)`）。
   - `POST /jobs/{job_id}/generate-application-kit` → 200（compute-and-save 動作比照 `/matches/run`）；生成部分失敗仍 200 + `missing`/`errors`（NFR-4 慣例）。
   - `GET /jobs/{job_id}/application-kit?resume_id=...` → 最新 kit；三類全無 → 404 `"Application kit not found."`。
   - `PATCH /artifacts/{artifact_id}` → 200 回新版本 `ArtifactResponse`。
   - 錯誤映射：`ResumeNotFoundError`/`JobNotFoundError`/`ArtifactNotFoundError` → 404；`KitResumeNotReadyError`/`KitJobNotIndexedError` → 409；訊息風格照抄 skill_gaps。
4. `app/main.py` 註冊 router（`include_router` 清單尾端）。

**驗收**：手動（或整合測試）走 POST → GET → PATCH → GET 驗證版本遞增與歷史保留。

---

### Step 9 — 前端（約 1.5 天）

**做法**：

1. `src/types/applicationKit.ts`：鏡像後端 schema（snake_case、檔頭註解標明鏡像來源——既有慣例），含三個 content 介面 + `ArtifactResponse` + `ApplicationKitResponse`。
2. `src/api/applicationKit.ts`：
   - `generateApplicationKit(jobId: string, resumeId?: string): Promise<ApplicationKitResponse>`（POST）
   - `fetchApplicationKit(jobId: string, resumeId: string): Promise<ApplicationKitResponse | null>`（GET；404 用 `axios.isAxiosError` 吞掉回 `null`——既有慣例）
   - `updateArtifact(artifactId: string, content: object): Promise<ArtifactResponse>`（PATCH）
3. 路由與入口：`App.tsx` 受保護區加 `/jobs/:jobId/application-kit → ApplicationKitPage`；`JobDetailPage` header 加「Application Kit」連結按鈕（`index_status !== 'indexed'` 時 disabled + 提示，比照 skill-gap 前置條件 UI）。
4. `src/pages/ApplicationKitPage.tsx`（沿用 `useEffect + useState` 模式；不啟用 TanStack hooks——與全站一致）：
   - 載入：`fetchCurrentResume()` → 無履歷則顯示導去 `/resume` 的提示；有則 `fetchApplicationKit(jobId, resumeId)`；`null` → 顯示「Generate Application Kit」空狀態。
   - 生成：`generating` 布林 → 按鈕 disabled、文案 `'Generating… (30–90s)'`、按鈕下方灰字提示 AI 呼叫較久（照抄 skill-gap 模式）；409 讀 `e.response.data.detail` 顯示；`missing.length > 0` → 黃色 banner 列出缺漏 + 「Regenerate」。
   - 三個區塊元件：`TailoredResumeCard`（依 section 分組列 bullet rewrites：original → improved + reason、keywords chips）、`CoverLetterCard`（intro / body 段落 / closing）、`InterviewPrepCard`（依 category 分組、題目可展開 answer outline——沿用 skill-gap 單開 accordion 模式）。match_score 顯示沿用 `scoreBadgeClass` 門檻配色（≥0.8 綠 / ≥0.5 黃 / 紅——已對齊 routing 門檻）。
   - **編輯（FR-40）**：每卡「Edit」切換編輯模式——cover letter 三欄 textarea；tailored resume 編輯 improved 文字；interview prep 編輯 question / outline。Save → `updateArtifact` → 以回傳新版本更新畫面、顯示 `v{n}` 徽章。
   - **匯出（FR-55）**：每卡 Copy 與 Download .md 按鈕。原理：前端把 structured content 組成 markdown 字串；Copy 用 `navigator.clipboard.writeText`；下載用 `new Blob([md], {type:'text/markdown'})` + `URL.createObjectURL` + 隱形 `<a download>` 點擊（用完 `revokeObjectURL`）。新增共用 util `src/lib/export.ts`（`copyText`、`downloadMarkdown`、三個 `xxxToMarkdown` 轉換函式）——repo 目前沒有任何 clipboard/下載工具，需新建。
   - 樣式全用既有 Tailwind 慣用字串（卡片 `space-y-4 rounded-lg bg-white p-6 shadow`、主按鈕 `rounded-md bg-blue-600 ...`、banner 紅/黃款式）。

**驗收**：手動走完 生成 → 檢視 → 編輯保存 → copy → 下載 .md。

---

### Step 10 — 測試（約 1 天）

**原理（scripted planner）**：agent 測試不能真打 Gemini。既有 `_FakeProvider` 只能假 wrapper 層；planner 層需要新的假物件——一個**照劇本回 `AIMessage(tool_calls=[...])` 序列**的聊天模型。做法：`class ScriptedPlanner(BaseChatModel)`，建構子收 `list[AIMessage]`，`_generate` 依序出隊，`bind_tools(...)` 回傳 self（記錄收到的 tools 供斷言「7 個都綁了」）。劇本完全決定 graph 走哪條路 → 測試可重現。放 `tests/agent_fakes.py` 供共用。

**做法**（新增 `tests/test_application_kit.py`，覆蓋 plan.md 要求的 (a)~(d) 四情境 + API/資料層）：

| # | 測試 | 驗證重點 |
|---|---|---|
| a | happy path：劇本 fetch→compute(0.65)→3×generate→3×save→結束 | 200；DB 有 3 rows（source="agent"、同 run_id、v1）；response 三欄非 null、missing 空 |
| b1 | 低分分支：compute_match 回 0.3 | messages 中出現 `[directive]` 且內容含「retrieve_job_evidence」；`directive_issued` 只發一次 |
| b2 | 高分分支：compute_match 回 0.9 | directive 內容為「跳過檢索直接生成」 |
| c | 工具失敗降級：`_FakeProvider` 注入 `StructuredOutputError`（cover letter） | 200；其餘兩類保存成功；`missing=["cover_letter"]`；errors 非空；不 crash |
| d | 鬼打牆保護：劇本無限重複呼叫 `fetch_resume` | `GraphRecursionError` 被捕捉；200 partial；run 有結束 |
| e | 完成檢查 re-prompt：劇本先只存 2 類就停 → 提醒後補存第 3 類 | reprompt 訊息注入、最終三類齊 |
| f | gates：resume 未 parsed / job 未 indexed | 409 + 對應 detail |
| g | resume_id 省略 → current resume 被使用 | 正確 resume_version_id 入庫 |
| h | 版本控：連跑兩次 → v1、v2 並存；PATCH → v3（source="edit"）、舊 row 原封不動 | append-only |
| i | PATCH 驗證：content 不符 schema → 422/400；他人 artifact → 404 | 隔離 + schema 驗證 |
| j | GET：無 kit → 404；有 → 各 kind 取最新版 | |
| k | user isolation：A 的 kit，B 看不到（404） | 既有 isolation 測試風格 |

   - fixtures：照 `test_skill_gaps.py` 模式——`use_provider` 覆蓋本 router 的 `get_llm_provider`、autouse 覆蓋 `get_reranker`（`_NoopReranker`）、新增覆蓋 `get_planner_model`（回 ScriptedPlanner）。
   - 另補 Step 3/4/5 的純函式與 schema 單元測試（歸入上表外的小測試）。

前端（`frontend/tests/ApplicationKitPage.test.tsx`，`vi.mock` 整個 api module）：(1) 空狀態顯示 Generate 按鈕、點擊後 pending promise 驗證 disabled；(2) 有資料時三區塊渲染；(3) 編輯 cover letter → save 呼叫 `updateArtifact` 帶正確 payload。jsdom 無 `navigator.clipboard`，export 測試需在測試內 stub（或只測 `xxxToMarkdown` 純函式——建議後者，簡單穩定）。

**驗收**：`pytest -q` 全綠（既有 104 + 新增 ≈15）；`npm test` 全綠；CI 綠燈。

---

### Step 11 — LangSmith 驗證與整體驗收（約 0.5 天）

**做法**：

1. 本地 `.env` 設 `LANGSMITH_TRACING=true` + API key，`docker compose up` 真跑一次 kit 生成。
2. 在 LangSmith 專案確認 trace 樹：LangGraph run → 各 node → planner 的 function-call 選擇 → 工具內 `gemini.generate_structured` 巢狀 span；score directive 訊息可見。若 LangGraph span 未出現 → 依 Step 1 第 4 點補 env 別名後重驗。
3. 手動 end-to-end：註冊 → 上傳履歷 → 建 job →（可先跑 match 看分數）→ Application Kit 頁生成 → 三區塊有內容、與 job 相關 → 編輯保存出 v2 → copy/.md 匯出 → 登出登入資料仍在 → 換帳號看不到。
4. 對照下方驗收清單逐項打勾。

**Phase 7 驗收清單**（對照 plan.md / SRS）：

- [ ] 一次 click 產出三類 artifacts 並保存（FR-56）
- [ ] 7 個工具、名稱與 FR-57 一致、由 LLM function calling 動態選擇（FR-66；graph 無硬寫順序）
- [ ] match score < 0.5 / ≥ 0.8 走不同 directive（FR-58）
- [ ] 工具失敗 run 不 crash、partial 結果照樣回傳（NFR-4）
- [ ] artifacts 歷史保留、可編輯出新版、可 copy/.md 匯出（FR-54~55、FR-40）
- [ ] planner 與生成呼叫皆入 `LLMCallLog`（FR-65）
- [ ] LangSmith trace 可見工具選擇與分岔（FR-69）
- [ ] 新增測試涵蓋 (a)~(d) 四情境；`pytest`、`npm test`、CI 全綠
- [ ] `LANGSMITH_TRACING` 未設時一切功能與測試正常（靜默停用）

---

## 6. 檔案異動總表

**新增（後端）**：`app/db/models/generated_artifact.py`、`alembic/versions/0009_generated_artifacts.py`、`app/ai/parsers/kit_schema.py`、`app/ai/prompts/kit.py`、`app/ai/agents/{context,tools,state,graph}.py`、`app/services/application_kit_service.py`、`app/schemas/application_kit.py`、`app/api/application_kits.py`、`tests/agent_fakes.py`、`tests/test_application_kit.py`

**新增（前端）**：`src/pages/ApplicationKitPage.tsx`、`src/api/applicationKit.ts`、`src/types/applicationKit.ts`、`src/lib/export.ts`、`tests/ApplicationKitPage.test.tsx`

**修改**：`backend/pyproject.toml`（依賴）、`app/db/models/__init__.py`（註冊）、`app/main.py`（router）、`tests/conftest.py`（TRUNCATE 清單）、`frontend/src/App.tsx`（路由）、`frontend/src/pages/JobDetailPage.tsx`（入口按鈕）；（視 Step 11 結果）`app/core/config.py`（env 別名）

---

## 7. 風險與緩解

| 風險 | 緩解 |
|---|---|
| `langchain-google-genai` 與舊 SDK `google-generativeai` 依賴衝突（無 lock file，CI/Docker 現解） | Step 1 先行安裝驗證 + import 冒煙測試 + 既有 provider 測試回歸；衝突時再評估替代層 |
| planner 對自訂 model 名（`gemini-3.5-flash-lite`）或 tool schema 相容性問題 | Step 1 用真 key 做一次最小 `bind_tools` 冒煙呼叫 |
| Agent 無限迴圈 / 燒 token | `recursion_limit=50` + `KIT_DEADLINE_SECONDS=240` + 完成檢查 re-prompt 上限 2 次；planner 呼叫全記帳可事後稽核 |
| 同步請求 30~90 秒 | 已拍板接受；前端明確 loading 提示；Phase 10 再談進度優化 |
| LLM 就是不肯存滿三類 | completion check 明確點名缺漏 re-prompt；仍缺則 partial + `missing` 讓前端引導重生成 |
| 三個 generate 內容品質不穩 | structured schema 全 default + 既有 repair/fallback 鏈；品質量化留給 Phase 9 rubric |
| conftest TRUNCATE 漏加新表 | Step 2 checklist 明列；測試互污染的典型症狀（跨測試看到殘留 rows）寫在此提醒 |

## 8. 建議時程（總計約 5.5~6 天，與 plan.md 相符）

| 天 | 工作 |
|---|---|
| D1 上午 | Step 1 依賴驗證 |
| D1 下午~D2 上午 | Step 2 model/migration + Step 3 schemas |
| D2 下午 | Step 4 prompts + Step 5 生成函式 |
| D3 | Step 6 tools |
| D4~D4.5 | Step 7 graph |
| D4.5~D5 | Step 8 service + API |
| D5~D6 上午 | Step 9 前端 |
| D6 | Step 10 測試補齊 + Step 11 驗收 |
