
---

# Software Requirements Specification (SRS)
## Project Title
**CareerPilot AI: AI-Powered Resume and Job Match Platform**


## Prepared For
SER 594: AI for Software Engineers Course Project

---

# 1. Introduction

## 1.1 Purpose
本文件定義 **CareerPilot AI** 的軟體需求。此系統面向求職者，提供履歷上傳、職缺匯入、職缺匹配、技能缺口分析、客製化履歷建議、Cover Letter 草稿生成、面試準備題生成等功能。本文件同時作為開發、測試、驗收的正式依據。
## 1.2 Scope
CareerPilot AI 是一個全端 AI 求職輔助平台。使用者可建立帳號、登入、上傳履歷、輸入目標職缺內容，系統會對履歷與職缺進行結構化解析，將職缺建立向量索引，進行職缺匹配排名，透過 RAG 找出技能缺口，再以 agent workflow 產生履歷修改建議、Cover Letter 草稿與面試準備題目。

## 1.3 Definitions, Acronyms, and Abbreviations
- **SRS**: Software Requirements Specification
- **RAG**: Retrieval-Augmented Generation
- **LLM**: Large Language Model
- **Embedding**: 將文字轉換為向量表示的方法
- **Vector Store**: 儲存向量並支援相似度檢索的資料庫
- **Structured Output**: 由 LLM 依 schema 輸出的 JSON 或 typed object
- **Agent Workflow**: 可做多步驟決策、選工具、執行任務的 AI orchestration 流程
- **Match Score**: 系統對履歷與職缺適配程度的量化分數

## 1.4 References
本 SRS 對齊以下要求：
- full-stack application
- authentication mandatory
- persistent data layer
- RAG / embeddings / agent / structured outputs 等 AI integration
- 15+ tests
- CI/CD（含 container image 發佈至 GHCR）
- deployment
- quantitative AI evaluation and system evaluation
- LLM observability / evaluation tooling（LangSmith）。

---

# 2. Overall Description

## 2.1 Product Perspective
CareerPilot AI 是一個獨立的 web-based full-stack system，由前端、後端、主資料庫、向量資料庫、檔案處理模組、AI orchestration 層與 evaluation 模組組成。系統不是單純聊天工具，而是完整求職工作流平台，符合對 multi-step workflows、persistent state、domain-specific logic 的要求。

## 2.2 Product Functions
系統主要提供以下高階功能：

1. 使用者註冊、登入、登出
2. 履歷上傳、文字抽取與結構化解析
3. 職缺內容匯入、結構化解析與向量索引
4. Job match ranking 與分數解釋
5. Skill gap analysis with RAG and source attribution
6. Tailored resume suggestions
7. Cover letter draft generation
8. Interview preparation question generation
9. AI 結果儲存與版本控管
10. 評估、測試、部署與監控支援。

## 2.3 User Classes and Characteristics
### 2.3.1 End User (Job Seeker)
- 主要使用者
- 不需技術背景
- 希望快速分析自己與職缺的匹配度
- 希望獲得可行、具體的履歷與面試建議

### 2.3.2 Admin / Developer
- 維護系統、查看 logs、執行 evaluation、修復流程錯誤
- 管理 deployment、CI、環境設定

## 2.4 Operating Environment
- 前端：現代瀏覽器（Chrome, Edge, Firefox, Safari）
- 後端：Dockerized Linux environment
- 主資料庫：PostgreSQL
- Vector store：pgvector
- 後端語言：Python
- 後端框架：FastAPI
- 前端框架：React（Vite）
- AI provider：Google Gemini（primary `gemini-3.5-flash-lite`，fallback `gemini-3.6-flash`），透過可替換 wrapper 抽象化，單一 `GEMINI_API_KEY`
- Observability / Evaluation：LangSmith（tracing 與 evaluation；未設定 API key 時靜默停用，不影響核心功能）
- Container registry：GitHub Container Registry（GHCR）
所有 build / local deployment 依賴必須可重現。

## 2.5 Design and Implementation Constraints
1. 系統不得只是 chatbot wrapper。
2. 系統不得只是 single API call。
3. 必須有 authentication。
4. 必須有 persistent storage。
5. 若使用 RAG/semantic search，必須有 vector store。
6. 必須可公開部署或以 Docker 本地重現。
7. 必須有至少 15 個 unit/integration tests。
8. 必須提供 quantitative AI evaluation 與 baseline comparison。
9. 必須有 CI。
10. 不得把 secrets 提交到 repository。
11. CI 應於 push 至 main 與 version tag 時自動 build production Docker images 並發佈至 GHCR；部署方式為 `docker compose pull && docker compose up`，不含雲端自動部署。

## 2.6 Assumptions and Dependencies
1. 使用者可提供可解析的履歷檔案（PDF / DOCX / text）。
2. 使用者可貼入或上傳 job descriptions。
3. LLM 與 embedding provider 在系統運行時可存取。
4. Docker、資料庫與 API key 會由團隊正確配置。
5. 評估資料集將由團隊自行建立至少 25 個 curated profiles / scenarios，以支援最終 evaluation。
6. LangSmith 帳號與 API key 可用（free tier 即可）；tracing / evaluation 為外部 SaaS 依賴，核心功能不得因其不可用而失效。

---

# 3. System Features and Functional Requirements

以下需求使用 **FR-x** 編號，並採用 **shall** 表述。

## 3.1 User Authentication and Account Management

### FR-1 User Registration
系統應允許新使用者以 email 與 password 完成註冊。

### FR-2 User Login
系統應允許已註冊使用者登入並建立受保護 session。

### FR-3 User Logout
系統應允許使用者登出並使當前 session 失效。

### FR-4 Session Isolation
系統應確保不同使用者的履歷、職缺與 AI 產物彼此隔離。

### FR-5 Protected Routes
未登入使用者不得存取受保護功能，包括履歷管理、職缺分析與 AI 生成結果。

### FR-6 Persistent User Identity
系統應在跨 session 後仍能辨識同一使用者並載入其歷史資料。

---

## 3.2 Resume Ingestion and Parsing

### FR-7 Resume Upload
系統應允許使用者上傳履歷檔案，至少支援 PDF、DOCX，並可選擇直接貼上文字。

### FR-8 Resume Text Extraction
系統應將上傳履歷轉換為純文字並保存原始內容與抽取結果。

### FR-9 Structured Resume Parsing
系統應使用 LLM 與 structured output schema 將履歷解析為結構化資料，包括但不限於：
- 基本資料
- Summary
- Skills
- Work Experience
- Projects
- Education
- Certifications

### FR-10 Resume Validation
系統應對解析結果做 schema 驗證，若輸出格式錯誤，應執行 retry 或 fallback parsing，而非直接失敗。

### FR-11 Resume Editing
系統應允許使用者在 UI 中檢查與手動修正履歷解析結果。

### FR-12 Resume Versioning
系統應保存履歷版本，以支援原始版本與 AI 建議版本之間的比較與回復。

---

## 3.3 Job Description Ingestion and Indexing

### FR-13 Job Input
系統應允許使用者新增 job description，支援文字貼上。

### FR-14 Job Parsing
系統應將 job description 解析為結構化欄位，包括但不限於：
- Company
- Job title
- Responsibilities
- Required skills
- Preferred skills
- Qualifications
- Experience requirements
- Location / work mode

### FR-15 Job Chunking
系統應將 job description 切分為具語意的 chunks，並保留 section metadata。

### FR-16 Job Embedding Generation
系統應為每個 job chunk 產生 embeddings。

### FR-17 Vector Indexing
系統應將 job embeddings 寫入 vector store 並支援後續相似度搜尋。

### FR-18 Persistent Job Storage
系統應將 job 原文、解析結果、chunks 與索引 metadata 持久化保存。

---

## 3.4 Job Matching and Ranking

### FR-19 Match Execution
系統應允許使用者選擇一份履歷與一個或多個職缺來執行匹配分析。

### FR-20 Match Score
系統應為每個履歷-職缺配對計算 match score，分數至少反映：
- embedding similarity
- required skill coverage
- preferred skill coverage
- experience alignment
- optional rerank score

### FR-21 Ranked Results
系統應提供依 match score 排序的 job list。

### FR-22 Match Explanation
系統應對每個 match 結果提供可讀解釋，包括：
- 為何匹配
- 主要 skill overlap
- 缺少的關鍵技能
- 風險或弱點

### FR-23 Match Persistence
系統應保存匹配結果，以便使用者日後查看與比較。

---

## 3.5 Skill Gap Analysis with RAG

### FR-24 Skill Gap Workflow
系統應能針對特定職缺執行 skill gap analysis。

### FR-25 Retrieval
系統應從 vector store 中檢索與 skill gaps 相關的 job chunks。

### FR-26 Re-ranking
系統應支援 retrieval result re-ranking，以提升 evidence relevance。

### FR-27 RAG Generation
系統應使用檢索結果與履歷結構化資料生成技能缺口分析。

### FR-28 Source Attribution
系統應對每項 skill gap 提供 source attribution，至少指出相對應 job evidence。

### FR-29 Gap Severity
系統應將 skill gap 區分為不同嚴重程度，例如 high / medium / low。

### FR-30 Improvement Suggestions
系統應為 skill gap 提供補強建議。

---

## 3.6 AI-Generated Resume Suggestions

### FR-31 Tailored Suggestions
系統應針對特定職缺產生客製化履歷建議，而非一般性履歷建議。

### FR-32 Section-Level Suggestions
系統應將建議分配到具體 section，例如 Summary、Experience、Projects、Skills。

### FR-33 Bullet Rewrite Suggestions
系統應提供可直接採用的 bullet rewrite 建議。

### FR-34 Keyword Suggestions
系統應指出建議加入或強化的關鍵字與技能表述。

### FR-35 Reasoning Evidence
每項建議應盡可能附上原因，且應與 job requirement 或 skill gap analysis 對齊。

### FR-36 Save Suggestions
系統應保存產生的履歷建議與其對應 job context。

---

## 3.7 Cover Letter Generation

### FR-37 Cover Letter Draft
系統應能針對特定 job 生成 cover letter draft。

### FR-38 Personalization
Cover letter 應依下列資訊客製化：
- 履歷內容
- target job requirements
- match / skill gap 分析結果

### FR-39 Structured Cover Letter Output
系統應至少以結構化區塊管理 cover letter，如 intro、body、closing，以利編輯與測試。

### FR-40 Editable Draft
使用者應能在 UI 中編輯 cover letter，並保存最終版本。

---

## 3.8 Interview Preparation Generation

### FR-41 Interview Questions
系統應能根據職缺與履歷生成面試準備題目。

### FR-42 Question Categories
系統應至少支援以下類型：
- technical
- behavioral
- project-based
- skill-gap-focused

### FR-43 Structured Output for Questions
每題應至少包含：
- question
- category
- why it matters
- related resume area
- suggested answer outline

### FR-44 Persist Interview Prep
系統應保存已產生的 interview preparation artifacts。


---

## 3.9 Result Management

### FR-45 Artifact History
系統應保存所有生成 artifacts 的歷史紀錄。

### FR-46 Export Capability
系統應至少支援將生成內容複製或匯出為可讀格式。

---

# 4. External Interface Requirements

## 4.1 User Interface Requirements
系統前端應提供以下頁面或等價介面：

1. 登入 / 註冊頁
2. Dashboard
3. Resume Management Page
4. Job Management Page
5. Job Detail / Match Analysis Page
6. Application Kit Page

UI 應提供明確操作流程。

## 4.2 API Interface Requirements
前後端應透過定義良好的 API 溝通，建議採 REST。核心 endpoints 包括：

- `/auth/register`
- `/auth/login`
- `/auth/me`
- `/resumes/upload`
- `/resumes/current`
- `/jobs`
- `/matches/run`
- `/jobs/{id}/skill-gap`
- `/jobs/{id}/generate-application-kit`

## 4.3 Hardware Interfaces
無特殊硬體需求。一般個人電腦與雲端部署環境即可。

## 4.4 Software Interfaces
- PostgreSQL
- pgvector
- LLM provider API
- Embedding API
- LangSmith（tracing / datasets / evaluation runs）
- Docker / docker-compose
- CI/CD system (GitHub Actions)
- GitHub Container Registry（production image 發佈）
明確要求 tests 可單一命令執行、CI 在每次 push 執行，且 push 至 main / version tag 觸發 image build 與 GHCR 發佈。

---

# 5. Data Requirements

## 5.1 Primary Entities
系統至少應包含以下主要資料實體：
- User
- Resume
- ResumeVersion
- Job
- JobChunk
- JobEmbedding
- MatchResult
- SkillGapReport
- GeneratedArtifact
- LLMCallLog
- ResumeEmbedding

## 5.2 Persistence Requirements
所有資料必須持久化保存，不得僅存在記憶體。此要求包括：
- 使用者帳號
- 履歷原文與解析結果
- 職缺原文與解析結果
- 向量索引
- 匹配結果
- AI 生成內容


## 5.3 Data Integrity Requirements
1. 所有資料應帶 user ownership。
2. 每個 artifact 應與來源履歷、job、使用者關聯。
3. 結構化輸出應有 schema validation。
4. 刪除或替換版本時應避免破壞歷史紀錄。

---

# 6. AI and Workflow Requirements

## 6.1 Required AI Techniques
本系統將實作並聲明以下 AI techniques：

1. **Vector Search / Embeddings**
2. **RAG**
3. **AI Agents / Multi-Step Workflows**
4. **Prompt Engineering with Structured Outputs**
5. **LLM API Integration**
6. **Function Calling / Tool Use**


## 6.2 Agent Requirements
### FR-47 Multi-Step Agent
系統應實作至少一個 agent workflow（以 LangGraph 實作），負責從 job 與履歷資料中決定並執行多步驟任務。Application kit agent 的單次執行應產出並保存全部三類 artifacts（tailored resume suggestions、cover letter、interview prep）。

### FR-48 Tool Selection
該 agent 應可使用恰好 7 種工具：
1. `fetch_resume` — 取得履歷結構化資料
2. `retrieve_job_evidence` — RAG 檢索 job chunks
3. `compute_match` — 計算 match score
4. `generate_tailored_resume` — 生成客製化履歷建議
5. `generate_cover_letter` — 生成 cover letter
6. `generate_interview_qs` — 生成面試準備題
7. `save_artifact` — 保存生成 artifacts

### FR-49 Conditional Logic
agent 應可根據 match score 或 skill gaps 決定後續流程，例如先做 gap analysis 再做 resume tailoring。

### FR-57 LLM-Driven Tool Selection (Function Calling)
Agent 的工具選擇應由 LLM 透過 native function calling（tool binding）在 FR-48 所列 7 個工具間動態決定；graph 不得以固定順序硬性串接全部工具，僅允許以 match score 為條件的 routing（FR-49）與安全防護（step limit、timeout）約束 LLM 的選擇空間。

## 6.3 Structured Output Requirements
### FR-50 Structured Parsing
履歷解析、職缺解析、skill gap report、interview prep 與 cover letter 都應使用可驗證 schema 的 structured outputs。

### FR-51 Malformed Output Handling
若 LLM 回傳格式不符，系統應自動 retry 或 fallback。

### FR-52 Auditability
系統應保存解析失敗或 schema validation failure logs 供除錯與評估。

## 6.4 LLM API Wrapper Requirements
### FR-53 Provider Abstraction
系統應使用 provider-agnostic wrapper 封裝 LLM 呼叫，使模型可替換。

### FR-54 API Key Security
API keys 不得寫入程式碼庫，應使用 environment variables。

### FR-55 Retry Logic
系統應對 transient API failure 執行 retry。

### FR-56 Cost and Token Tracking
系統應記錄 token usage、latency 與估計成本。

### FR-58 Model Fallback Chain
Wrapper 應實作同 provider 雙模型 fallback：primary `gemini-3.5-flash-lite`；當 transient-error retry 耗盡，或 structured output 的 schema validation retry 與 JSON repair 皆失敗時，應以 `gemini-3.6-flash`（較強模型，free tier 額度僅作救援用）完整重試該操作一次。Fallback 僅適用 `generate` / `generate_structured`（embedding 無 fallback 模型）。實際使用之 model 與 `fallback_used` 應記錄於 LLMCallLog。（實作採廣義觸發：primary 完整流程之任何失敗——含安全機制阻擋、空回應、model 設定錯誤——皆觸發 fallback，為上述兩種情境之超集。）

### FR-59 Malformed-Response Rate Tracking
LLMCallLog 應記錄每次呼叫的 `attempts`、`repair_used`、`fallback_used` 與最終 status；token 用量與成本估計跨所有生成嘗試加總（失敗嘗試亦計費），成本按各次嘗試實際使用模型之單價分別計算。系統據此可計算：
- **raw malformed rate**：首次嘗試即 schema validation 失敗之比率
- **final malformed rate**：經 retry / repair / fallback 後仍失敗（StructuredOutputError）之比率

Final malformed rate 以 < 1% 為 measure-and-report 目標，於 eval report 報告實測值，非硬性驗收門檻。

## 6.5 Observability and Tracing

### FR-60 LLM Observability and Tracing
所有 LLM wrapper 呼叫（generate / generate_structured / embed）與 agent graph 執行應可透過 LangSmith tracing 觀測：wrapper 方法以 `@traceable` 裝飾，LangGraph 以環境變數自動 trace。未設定 `LANGSMITH_API_KEY` / `LANGSMITH_TRACING` 時應靜默停用，不影響功能與測試。

---

# 7. Non-Functional Requirements

## 7.1 Performance
### NFR-1 Latency
系統應量測至少以下操作的 p50 與 p95 latency：
- resume parsing
- job indexing
- match generation
- application kit generation
最終需報告 key user actions 的 latency。

### NFR-2 Responsiveness
一般 UI 操作應在合理時間內回應，長任務應顯示 loading/progress 狀態。

## 7.2 Reliability
### NFR-3 Error Handling
系統應在 LLM、embedding、檔案處理與資料庫失敗時提供可理解錯誤訊息。

### NFR-4 Graceful Degradation
單一 AI 模組失敗不應導致整體系統 crash。

### NFR-5 Error Rate Tracking
系統應可統計正常使用情況下的 error rate。

## 7.3 Security
### NFR-6 Credential Security
不得在 repository 中提交 API keys、密碼或憑證。

### NFR-7 Access Control
所有使用者資料應受身份驗證與授權保護。

### NFR-8 Data Isolation
不同使用者之間資料不得互相可見。

## 7.4 Maintainability
### NFR-9 Modular Architecture
系統應採模組化設計，分離 frontend、backend、eval、tests 與 docs。

### NFR-10 Repository Structure
repository 應遵守 monorepo 或最多兩個 repo 的限制，且結構清楚。

### NFR-11 Code Quality
系統應使用 formatter、明確依賴管理與一致命名規則。

## 7.5 Testability
### NFR-12 Automated Tests
系統應具備至少 15 個 unit/integration tests，涵蓋 API、data layer、AI pipeline、authentication。後端 test coverage 以 ≥80% 為工作目標（working target），實測值須量測並報告於 eval report；此為 measure-and-report 項目。

### NFR-13 CI
系統應在每次 push 由 GitHub Actions 自動執行測試。

## 7.6 Deployability
### NFR-14 Public or Docker Deployment
系統應能提供公開 URL，或提供可完整重現的 Docker deployment。

### NFR-15 Setup Reproducibility
README 與 `.env.example` 應足以在乾淨環境中建置並執行系統。

### NFR-16 Continuous Delivery
GitHub Actions 應在 push 至 main 與 `v*` version tag 時，build production Docker images（backend、frontend）並推送至 GHCR；部署以 `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d` 一鍵完成。

---

# 8. Testing and Evaluation Requirements

## 8.1 Test Suite Requirements
### TR-1 Minimum Test Count
系統應提供至少 15 個 unit/integration tests。

### TR-2 Coverage Areas
測試應至少覆蓋：
- authentication
- API endpoints
- data layer operations
- AI pipeline components

### TR-3 Single Command Execution
所有測試應能以單一命令執行。

### TR-4 CI Execution
所有測試應在 GitHub Actions 中自動執行。

## 8.2 AI Evaluation Requirements
### ER-1 Quantitative Metrics
系統應定義並計算至少兩種 quantitative AI metrics。

### ER-2 Baseline Comparison
所有主要 AI metrics 應與 baseline 比較。

### ER-3 Suggested Metrics for This Project
本專案至少應實作以下兩項：
1. **Job matching relevance metric**
   Precision@K 與 MRR，與 TF-IDF keyword-only baseline 比較，並報告改善百分比（參考目標 35%，measure-and-report，非驗收門檻）
2. **Resume suggestion quality metric**
   例如 rubric-based average score

### ER-4 Evaluation Dataset
應建立至少 25 組 curated profiles / job scenarios 用於 evaluation。

### ER-5 RAG Retrieval Quality
對 skill-gap retrieval，應以 ground-truth relevant chunks 標註計算 Precision@K 與 MRR，並比較 rerank 前後之差異。

### ER-6 Hallucination / Faithfulness Detection
應以 LLM-as-a-judge（judge model 使用 `gemini-3.6-flash`，較 primary 強一級之同 provider 模型）逐條檢驗 skill-gap claims 是否被其 cited chunks 支持（supported / partially supported / unsupported），並報告 hallucination rate（= unsupported / total）。此評估應透過 LangSmith evaluator 執行並保留 traces。

### ER-7 Malformed-Response Rate Reporting
應自 LLMCallLog 統計 raw 與 final malformed-response rate（定義見 FR-59），於 eval report 報告；final rate 以 < 1% 為 measure-and-report 目標。

### ER-8 LangSmith Evaluation Infrastructure
Eval dataset 應同步至 LangSmith datasets，judge 類評估經 `langsmith.evaluate()` 執行；本地 JSON 為 source of truth，離線（無 API key）時 quantitative metrics 仍可本地計算，judge 類評估得跳過並於 report 註明。

## 8.3 System Evaluation Requirements
### ER-9 System Metrics
應報告：
- p50 latency
- p95 latency
- error rate
- backend test coverage percentage（目標 ≥80%，報告實測值）
這些是要求的 system-level quality 指標。

---

# 9. Architecture Requirements

## 9.1 High-Level Architecture
系統應包含以下核心層：

1. **Frontend Layer**
   - React
   - dashboard, forms, results, editing interface

2. **Backend API Layer**
   - authentication
   - business logic
   - orchestration
   - persistence

3. **AI Services Layer**
   - resume parser
   - job parser
   - embedding/indexing
   - retrieval/rerank
   - agent workflow
   - content generation

4. **Data Layer**
   - PostgreSQL
   - pgvector
   - file storage

5. **Evaluation Layer**
   - metrics scripts
   - baseline comparison
   - reporting outputs



## 9.2 Recommended Repository Structure
```text
project-root/
├── frontend/
├── backend/
├── eval/
├── tests/
├── docs/
├── .github/workflows/
├── docker-compose.yml
├── .env.example
└── README.md
```


---

# 10. Acceptance Criteria

本系統被視為完成並可提交，至少需滿足以下驗收條件：

1. 使用者可完成註冊、登入，且不同帳號資料隔離。
2. 使用者可上傳履歷並取得可編輯的結構化解析結果。
3. 使用者可新增 job descriptions，系統可建立索引並保存。
4. 系統可產生職缺匹配排名與 match explanation。
5. 系統可執行 skill gap analysis，且有 source attribution。
6. 系統可針對特定 job 產生 resume suggestions、cover letter 與 interview prep。
7. 系統至少實作 3 種以上有深度的 AI techniques。
8. 系統具備至少 15 個自動化測試與 CI；後端 coverage 以 ≥80% 為工作目標並報告實測值。
9. 系統可以 Docker 一鍵重現，且 CI/CD 於 main / version tag 自動發佈 images 至 GHCR。
10. 系統提供 quantitative AI metrics 與 baseline comparison，至少含：matching Precision@K / MRR vs keyword baseline（報告改善 %）、RAG retrieval Precision@K / MRR、hallucination rate、rubric score、malformed-response rate。
11. Application kit agent 以 LLM function calling 在 7 個工具間動態選擇，並依 match score 條件分流；LangSmith 可觀測完整 trace。


---

# 11. Risks and Mitigation

## Risk 1: LLM 輸出不穩定
**Mitigation:** structured outputs、schema validation、retry、fallback parser

## Risk 2: Job matching 品質不足
**Mitigation:** 混合 ranking strategy、reranking、人工標註資料集與 baseline evaluation

## Risk 3: 成本過高或 API 不穩
**Mitigation:** provider wrapper、token tracking、快取、限制生成長度

## Risk 4: Fallback 模型成本較高
**Mitigation:** fallback 僅在 primary retry 耗盡後觸發、LLMCallLog 追蹤 `fallback_used` 比率、pricing 表含 `gemini-3.6-flash` 價目

## Risk 5: Ground-truth 標註成本（relevant chunks / ranking）
**Mitigation:** 25 組 dataset 自 Week 1 起零散累積、標註 guideline 文件化、chunk 級標註僅針對 skill-gap queries

---
