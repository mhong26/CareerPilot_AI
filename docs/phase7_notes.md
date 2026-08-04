# Phase 7 開發筆記 — Agent Workflow: Application Kit（FR-31~44、FR-45~49、FR-57）

範圍：LangGraph agent（planner ↔ 7 tools 的 ReAct 迴圈 + match-score routing）
→ 三類 artifacts 生成與 append-only 持久化 → 三個 API endpoint → 前端
Application Kit 頁（檢視／編輯／匯出）。規劃書見 `phase7_plan.md`。

---

## 決策

### 決策 1：雙 LLM 通道並存——planner 走 langchain，生成走自家 wrapper
Planner 用 `ChatGoogleGenerativeAI(...).bind_tools(7 tools)`（新增依賴
`langchain-google-genai>=2.0`）；三個 generate 工具內部仍呼叫既有
`GeminiProvider.generate_structured`。
- **理由**：FR-57 要求 native function calling，自家 `LLMProvider` ABC 沒有
  `bind_tools` 介面，硬加等於重寫一套 tool-calling 協定；反過來讓生成也走
  langchain 則會失去 Phase 2/2R 建好的四層防禦（tenacity 網路重試 →
  validation retry → JSON repair → model fallback）與 `LLMCallLog` 記帳。
  兩者職責不同：planner 要的是「選工具」的結構化決策，generate 要的是
  「產出可驗證 payload」的韌性。
- **代價**：兩套 SDK 共存（`google-generativeai` 舊 SDK + langchain 的新
  SDK 家族），需驗證相容性（見問題 2）；planner 的重試與記帳要自己補
  （決策 8）。

### 決策 2：自訂 executor node，不用 prebuilt `ToolNode`
`graph.py::execute_tools` 手寫逐一執行 `tool_calls`。
- **理由**：三件事必須發生在同一處，prebuilt 節點都不便做——(a) 工具拋錯
  轉成錯誤 `ToolMessage` 讓 planner 看得見並改變策略（NFR-4，不是整個 run
  crash）；(b) `compute_match` 成功後把分數同步進 state 供 conditional edge
  路由（FR-49）；(c) 錯誤時 rollback 共享 session（見問題 6）。
- **代價**：少了 prebuilt 的維護紅利，但省下的是「為了繞開 ToolNode 而在
  外圍補三個 wrapper」的複雜度。

### 決策 3：Closure 工廠 `build_kit_tools(ctx)` + 精簡回傳原則
7 個工具定義在工廠函式內，closure 捕獲 `KitRunContext`；工具完整產物寫進
ctx，回給 LLM 的 `ToolMessage` 只放幾百字摘要。
- **理由（安全）**：`db` / `user` / `provider` 絕不能出現在 LLM 看得到的參數
  表——LLM 不該有能力指定 `user_id`（跨使用者存取），且 db session 本來就
  不是可序列化的文字。
- **理由（成本與注意力）**：planner 每一輪都重讀整份對話。把整份履歷或整組
  建議塞回訊息串會線性放大 token 消耗，且稀釋 planner 對「下一步該做什麼」
  的判斷。planner 只需要知道「做完了、關鍵數字是什麼」。

### 決策 4：`GeneratedArtifact` 採 append-only，與 MatchResult/SkillGapReport 的 upsert 刻意分歧
每次生成或編輯都 INSERT 新 row，永不 UPDATE content、永不 DELETE；最新版
= 同 `(resume_id, job_id, kind)` 下 `version_number` 最大者。
- **理由**：MatchResult / SkillGapReport 是「分析快照」——重跑代表舊分析已
  過時，覆蓋才是正確語意。Artifacts 是「創作產物」，FR-45 明文要求保存歷史、
  SRS §5.3.4 要求「刪除或替換版本時應避免破壞歷史紀錄」。使用者改了三次
  cover letter 後想回到第一版，是真實需求。
- `run_id` 讓同一次 agent run 的三件產物可歸組；編輯版沿用原 `run_id`、
  `source="edit"` 區分來源。

### 決策 5：score directive 是「注入建議訊息」而非強制路徑
`route_on_match_score` 觸發 `inject_directive` node，往對話插一則
`[directive]` 開頭的 `HumanMessage`，然後回 planner——不是把流程導向特定工具。
- **理由**：FR-57 禁止 graph 硬性串接工具順序，只允許 match score routing
  （FR-49）與安全防護。注入訊息後最終選擇權仍在 LLM，兩條要求同時滿足。
- 三個分支：`< 0.5` 建議先檢索證據再 tailor；`>= 0.8` 建議跳過檢索直接生成；
  中間帶只說「自行判斷」，刻意保留 LLM 決策空間。
- 用 `HumanMessage` 不用中途 `SystemMessage`：Gemini 的 system instruction
  轉換對對話中段插入的 system 訊息不友善。

### 決策 6：完成檢查的「判斷」在 conditional edge、「動作」在 node
`route_after_planner` 唯讀地決定 END / `reprompt`；寫入（注入 reminder、
`reprompt_count += 1`）在獨立的 `reprompt` node。
- **理由**：若判斷與寫入同節點，edge 無法分辨「剛重提示過（該回 planner）」
  與「重提示已用盡（該結束）」——兩者的 state 在寫入後長得一樣。決策唯讀是
  LangGraph 的慣用分工。

### 決策 7：`save_artifact` 逐 artifact commit，放棄「單一最終 commit」不變式
- **理由**：Phase 5/6 的 service 能維持「所有 LLM 呼叫在前、最後單一 commit」
  是因為流程是線性的。Agent run 天生交替「LLM 呼叫（`record_call` 自帶
  commit）」與「寫入」，且步數由 LLM 決定，該不變式在此不可能維持。
- **好處**：每件 artifact 是獨立原子單位——run 在第三件失敗時，前兩件仍然
  有效可用（NFR-4 的實質體現，而非只是「不 crash」）。

### 決策 8：planner 的 LLM 呼叫也寫進 `LLMCallLog`
`operation="agent_planner"`（`LLMCallLog.operation` 值域新增第四個值）。
- **理由**：FR-56 要求記錄 token / latency / 成本。一次 kit run 的 planner
  呼叫次數與生成呼叫相當（實測 8~9 次），漏記會讓 Phase 8 的成本報告嚴重
  低估。`usage_metadata` 缺失時記零值而非跳過，保持「一次呼叫一筆」的可數性。

### 決策 9：`resume_id` 可省略、預設 current resume（與 skill-gap 的強制參數分歧）
- **理由**：Phase 5 決策 18 定下「不做隱式 current resume」，但那是針對
  「一份履歷 × 一批職缺」的批次比對——履歷是主體，必須明指。Kit 的心智模型
  是「對這個職缺做申請包」，職缺是主體，履歷幾乎總是「我現在這份」。
  API 仍接受明確 `resume_id`（兩條路徑都有測試）。

### 決策 10：前端獨立路由頁，不做 JobDetail 第三個 tab
`/jobs/:jobId/application-kit`（使用者拍板）。三區塊 × 可編輯 × 可匯出的
內容量在單一 tab 裡會過度擁擠，也最貼 SRS §4.1 的頁面清單。JobDetail 保留
入口連結。

### 決策 11：前端沿用 async 函式 + `useState`，不啟用 TanStack Query
專案雖裝了 `@tanstack/react-query` 並掛了 Provider，但全站零使用。
- **理由**：一致性優先。在單一新頁面引入第二套資料存取範式，會讓之後的
  維護者面對兩種模式；要遷移就該全站一起遷移，那是獨立工作而非 Phase 7 的
  夾帶。

### 決策 12：`ctx.saved`（DB 為準）是完成檢查的事實來源，不複製進 state
`KitState` 只放流程控制旗標（`match_score` / `directive_issued` /
`reprompt_count` / `timed_out`）。artifact 是否已保存直接讀 `ctx.saved`。
- **理由**：單一事實來源。若 state 也存一份，兩者在「save 成功但 state 更新
  失敗」時會分歧，而完成檢查依據的是「DB 裡真的有沒有」。

### 決策 13：測試用 `ScriptedPlanner` 假聊天模型
`tests/agent_fakes.py` 實作 `BaseChatModel`，照劇本依序回傳預錄的
`AIMessage(tool_calls=[...])`；`bind_tools` 回傳 self 並記錄收到的 tools
（供斷言「7 個都綁了」）。
- **理由**：agent 的執行路徑完全由 LLM 決定，真打 API 則貴、慢、不可重現。
  劇本讓四條關鍵路徑（happy path / 兩個分數分支 / 工具失敗 / 鬼打牆）都成為
  確定性測試。這是既有 `_FakeProvider`（wrapper 層）之外新增的第二層假物件
  ——兩層對應決策 1 的兩條通道。

### 決策 14：三重保險絲，各擋不同的失控模式
`recursion_limit=50`（圖的總步數，擋 LLM 鬼打牆）、`KIT_DEADLINE_SECONDS=240`
（掛鐘時間，擋單步很慢而步數不多的情形）、`_MAX_REPROMPTS=2`（完成檢查的
重提示次數，擋 planner 反覆「以為做完了」）。
- **理由**：三者不可互相取代。步數上限管不到每步 30 秒的慢呼叫；時間上限
  管不到 3 秒內空轉 50 次；前兩者都管不到「planner 每次都乖乖結束但就是不
  存滿三類」的迴圈。

### 決策 15：編輯路徑嚴格驗證，生成路徑維持容錯（審查後追加）
`update_artifact` 拒絕未知鍵與缺鍵（→ 422），生成路徑的 kit_schema 維持
「全欄位有 default」。
- **理由**：兩條路徑的容錯理由相反。LLM 輸出不完整是常態，寬鬆驗證讓部分
  產物仍可入庫；使用者 PATCH 的語意是「用這份完整 content 取代」，寬鬆驗證
  會把打錯的欄位名靜默變成一版空白 artifact（見問題 8）。

### 決策 16：版號用 unique constraint + 重試，不用鎖（審查後追加）
`uq_generated_artifacts_pair_kind_version` 擋重複版號，
`insert_artifact_version` 捕捉 `IntegrityError` 後 rollback 重讀重試一次。
- **理由**：撞版是罕見事件（毫秒級競態窗口），為它上鎖會讓每次寫入都付出
  代價。讓 DB 當裁判、失敗才重試，是樂觀併發控制的標準做法。constraint 的
  前導欄位 `(resume_id, job_id, kind)` 同時充當「查最新版」的索引，不另建。

### 決策 17：規劃前先做 7 路平行程式碼偵察
規劃書的每個「重用既有元件」宣稱都先經 subagent 實地查證（LLM wrapper /
DB & migrations / services & RAG / API 層 / 依賴設定 / 前端 / 測試）。
- **價值**：偵察直接翻出三個規劃階段就該知道的事實——`MatchResult.match_score`
  早已為 0.5/0.8 routing 設計成 top-level float、`run_matches` 與
  `retrieve_job_chunks` 的 docstring 已預告 Phase 7 會直接呼叫、conftest 的
  TRUNCATE 清單漏加新表會導致測試互相污染。憑 plan.md 想像寫規劃會漏掉最後
  這項。

---

## 問題

### 問題 1：Gemini 因 schema 無 required 欄位而「合法偷懶」，只生成第一個欄位
真實 e2e 首跑發現：cover letter 整封塞進 `intro`、`body_paragraphs` 為空；
tailored resume 只回 `overall_strategy` + `top_keywords`，`section_suggestions`
整個消失。**加強 prompt 完全無效**（加了「never leave body_paragraphs empty」
仍舊）。
- **原因**：kit_schema 每個欄位都有預設值（決策沿用 Phase 3~6 的容錯慣例），
  導致 Pydantic 產生的 JSON schema **完全沒有 `required` 清單**；
  `to_gemini_schema` 也不會補。Gemini 的 constrained decoding 看到所有欄位
  皆非必填，生成第一個欄位後即可合法收束——模型不是不會寫，是規則允許它
  停手。prompt 是「建議」，schema 是「規則」，規則贏。
- **修正**：`schema_utils.to_gemini_schema` 在每層 object 補上
  `required = list(properties.keys())`。**生成側**強制模型每欄都產出、
  **驗證側** Pydantic 預設值照樣容錯——兩者職責不同，不衝突。
- **效果**：修正後 3 個 sections（含 bullet rewrites）、intro + 2 段 body +
  closing、4 題涵蓋四類，全部到位。此修正同時惠及 Phase 3~6 的履歷／職缺／
  skill gap 解析（同一個轉換函式）。
- **教訓**：structured output 的行為由 schema 決定，prompt 只能在 schema
  允許的空間內影響結果。產出「缺欄位」時先看 schema，不要一直改 prompt。

### 問題 2：新舊兩套 Google SDK 共存的相容性
`langchain-google-genai` 依賴新的 Google SDK 家族，而 repo 既有 wrapper 用
的是舊版 `google-generativeai`；專案又無 lock file（Docker/CI 每次從 PyPI
現解）。
- **處理**：把相容性驗證排在 Step 1 第一件事而非最後——安裝後跑
  `import google.generativeai` + `from langchain_google_genai import
  ChatGoogleGenerativeAI` 冒煙測試，再跑 `test_gemini_provider.py` 確認舊
  wrapper 未受影響。結果兩者可共存（舊 SDK 只發 `FutureWarning`）。
- 另補一次真 key 的最小 `bind_tools` 呼叫，確認 `gemini-3.5-flash-lite` 這個
  model 名確實支援 function calling（回傳了兩個 `tool_calls`）。附帶發現：
  該模型固定 sampling 參數，`temperature=0` 會被忽略並發 `UserWarning`——
  不影響功能（planner 的決策本來就穩定）。

### 問題 3：`add_messages` 依 message id 去重，鬼打牆測試假不起來
`ScriptedPlanner` 的 `loop_last=True` 模式重複回傳同一個 `AIMessage` 物件時，
graph 不會累積訊息、recursion limit 永遠觸發不了。
- **原因**：LangGraph 的 `add_messages` reducer 以 `message.id` 判斷「這是新
  訊息還是既有訊息的更新」——同 id 視為更新、就地取代，不追加。
- **修正**：每次迴圈用 `model_copy(update={"id": f"loop-{n}"})` 產生帶新 id
  的副本。

### 問題 4：`tools.py` ↔ `application_kit_service.py` 循環 import
工具層要呼叫 service 的生成函式，service 的 `run_application_kit` 又要
`build_kit_tools`。
- **修正**：`run_application_kit` 內部延遲匯入 agent 模組（函式內 import）。
  方向性清楚：agent 層依賴 service 層是常態，反向依賴只發生在編排入口這一
  個函式，用延遲匯入標記出來比拆第三個模組簡單。

### 問題 5：測試預期 404 但實際 409——gate 順序的語意問題
「沒有履歷的使用者對別人的 job 發請求」我原本預期 404（隔離優先），實際回
409。
- **原因**：`run_application_kit` 先解析履歷再查 job，履歷 gate 先觸發。
- **判斷**：這其實是**更好**的行為，不是 bug。沒有履歷的使用者無論對哪個
  job_id（存在或不存在、自己的或別人的）都得到同一個 409，不洩漏他人 job
  的存在性；若改成先查 job，反而能用回應碼探測「這個 job id 存在嗎」。
  改測試預期並把理由寫進 docstring。

### 問題 6：工具內的 DB 錯誤毒化共享 session，降級鏈整條變 500（審查發現）
`execute_tools` 捕捉工具例外轉成錯誤 `ToolMessage`，但沒有 rollback。
- **原因**：7 個工具共用同一個 `ctx.db` session，其中 `save_artifact`、
  `compute_match`（寫 MatchResult，帶 unique constraint）、
  `retrieve_job_evidence`（lazy 寫 embeddings）都會寫入。任一寫入拋
  `IntegrityError` 後 session 進入 pending-rollback 狀態，下一次 DB 操作
  （planner 的 `record_call`）直接拋 `PendingRollbackError`——這個例外不是
  `GraphRecursionError`，service 的唯一 except 攔不到，整個請求變 500，
  partial 結果全部丟失。**專為 NFR-4 設計的降級機制，反而保證了後續崩潰。**
- **為何測試沒抓到**：既有測試的假工具只拋 `RuntimeError`（不碰 DB），不會
  毒化 session。
- **修正**：executor 的 except 分支先 `ctx.db.rollback()`；`run_application_kit`
  再加一層 `except Exception` 保底（rollback 後照樣收集 `ctx.saved` 組 partial
  response）。新增回歸測試：工具內執行 `SELECT * FROM nonexistent_table`，
  斷言後續 planner 記帳仍全部成功。

### 問題 7：`version_number` 的 select-max+1 競態（審查發現）
兩個寫入端（agent 的 `save_artifact`、使用者的 PATCH）都是「查最大版號 +1
再 INSERT」，中間無鎖，且原設計刻意不設 unique constraint。
- **失效情境**：kit run 進行中（30~90 秒）使用者 PATCH 編輯，兩邊都讀到
  max=1 → 產生兩筆 v2 → `get_latest_kit` 只按 `version_number desc` 排序，
  回哪一筆不確定，使用者剛存的編輯可能從畫面上消失。
- **修正**：加 `uq_generated_artifacts_pair_kind_version` unique constraint
  （改 migration 0009，本機 downgrade/upgrade 往返驗證）+
  `insert_artifact_version` 統一兩個寫入端並在 `IntegrityError` 時重試 +
  `get_latest_kit` 補 `created_at desc` 防禦性 tiebreak。

### 問題 8：PATCH 沿用 LLM 容錯 schema，錯 shape 靜默存成空白版本（審查發現）
kit_schema 全欄位有 default 且忽略未知鍵——PATCH 送
`{"body_paragraphs": "..."}`（欄位名打錯或只送部分）會通過驗證，其餘欄位被
default 填成空字串，存出一版空白 artifact 且成為「最新版」。
- **修正**：見決策 15。`update_artifact` 先比對鍵集，未知鍵或缺鍵一律
  `ArtifactContentInvalidError` → 422；型別錯誤仍由 pydantic 把關。

### 問題 9：長履歷的 section 被截斷剩第一份工作（審查發現）
`_resume_sections` 原本把所有 experience 合成**一個**大區塊、所有 projects
合成另一個，而 prompt 端的 `_MAX_SECTION_CHARS = 2000` 是**逐區塊**套用——
五份工作經歷的候選人，第二份之後全部被砍掉，bullet rewrite 因此只能改第一
份工作。
- **修正**：一份 role / project 產生一個 tuple（名稱帶職稱與公司），讓既有的
  `_MAX_SECTIONS = 8` 與每塊 2000 字元預算按常數註解原本的意圖逐項套用。

### 問題 10：前端面試題大綱輸入框邊打字邊吃掉空格與換行（審查發現，最高嚴重度）
`answer_outline` 的 textarea 在 `onChange` 就做 `split('\n').map(trim)
.filter(Boolean)` 再 join 回字串顯示——使用者按 Enter 想空一行、或在句尾打
空格，字元立刻被吃掉，實質無法編輯。
- **原因**：把「儲存格式的轉換」綁在「每次按鍵」上。
- **修正**：編輯狀態改存原始字串（同一頁 `CoverLetterCard` 的 body 早已是
  這個做法），split/trim 只在 `handleSave` 組 payload 時做。新增回歸測試：
  輸入 `'Point A\n\nPoint B '` 斷言 textarea 原樣保留、存檔時才正規化成
  `['Point A', 'Point B']`。

### 問題 11：前端另外三個狀態管理缺陷（審查發現）
1. **卡片未以 artifact id 為 key**：重新生成 kit 後 React 沿用同一個元件實例，
   殘留的編輯草稿可能覆蓋剛產出的新內容。修正：`key={artifact.id}`，artifact
   換人時卡片整個重掛、編輯狀態自然歸零。
2. **kit GET 非 404 失敗被當成「尚未生成」**：網路錯誤時顯示空狀態與
   Generate 按鈕，使用者會白跑一次 agent（30~90 秒 + API 成本）去生成一份
   其實已經存在的 kit。修正：另立 `kitLoadFailed` 狀態顯示「請重整」。
3. **JobDetail 入口未依 `index_status` 禁用**：未索引職缺點進去必定 409。
   修正：非 `indexed` 時渲染成灰色不可點文字 + hover 提示（比照 skill-gap
   的前置條件 UI）。

### 問題 12：Docker image 未重建 → 註冊頁報「email 已被使用」
使用者回報註冊一直失敗、換 email 也沒用。
- **原因**：Phase 7 新增 `langchain-google-genai` 依賴，但 backend 容器跑的
  是加依賴之前建的 image，啟動時 `ModuleNotFoundError` 直接掛掉。後端從未
  真正起來，前端每個請求都連不上，被 catch-all 錯誤處理包成「email 可能已被
  使用」——症狀與真因毫無關聯。
- **修正**：`docker compose up -d --build backend`。
- **教訓**：`pyproject.toml` / `package.json` 有變動時，重啟容器必須帶
  `--build`。另：前端這句 catch-all 訊息在後端整個掛掉時會嚴重誤導，
  Phase 9 錯誤處理硬化時應分辨「連線失敗 / 409 email 重複」。

### 問題 13：pytest 洗掉開發資料庫的使用者資料
本機跑全套後端測試後，使用者原本註冊的帳號、履歷、職缺全部消失。
- **原因**：`conftest.py` 的 autouse fixture 在**每個測試後** TRUNCATE
  `users`、`resumes`、`jobs` 等資料表（測試隔離的既有設計），而 pytest 的
  `DATABASE_URL` 預設值就是 docker-compose 那個開發資料庫——同一個 DB。
  CI 因為每次都是全新容器所以無感，純粹是本機開發的地雷。
- **狀態**：本 phase 未修（超出範圍）。建議做法：獨立測試資料庫
  （`careerpilot_test`）讓 conftest 預設連它，或在 README 註明跑測試前先
  `export DATABASE_URL=...`。

### 問題 14：從 repo root 執行後端腳本會撞 `Settings` extra_forbidden
驗證腳本從專案根目錄跑時，pydantic 報 `postgres_user` / `postgres_password`
/ `postgres_db` / `vite_api_url` 四個 extra input 不被允許。
- **原因**：`SettingsConfigDict(env_file=".env")` 是**相對 cwd** 解析；cwd 為
  repo root 時會讀到根目錄 `.env`，而那份 `.env` 同時服務 docker-compose，
  含有應用程式 `Settings` 沒有定義的 `POSTGRES_*` / `VITE_API_URL`，而
  `BaseSettings` 預設 `extra="forbid"`。從 `backend/` 執行則找不到 `.env`、
  只讀 `os.environ`，正常運作；Docker 內 cwd 是 `/app` 且無 `.env`（環境變數
  由 compose 注入），也正常。
- **處理**：腳本一律從 `backend/` 執行。**潛在地雷**：若有人把根目錄 `.env`
  複製進 `backend/`，應用程式會啟動失敗；根治做法是 `Settings` 加
  `extra="ignore"`（未於本 phase 變更，避免夾帶非 Phase 7 的行為調整）。

### 問題 15：本機缺 `sentence_transformers`，rerank 走降級路徑
真實 e2e 的 `errors` 欄位出現
`rerank degraded to vector order: No module named 'sentence_transformers'`。
- **判斷**：**不是 bug，是降級設計在真實環境下的驗證**。本機 Python 環境沒裝
  該套件（Docker image 內有），reranker 載入失敗 → 依既有降級邏輯回退向量
  排序、記錄原因、run 照常完成。

---

## 驗證紀錄（2026-08-02）

- `pytest -q`（本機、真 Postgres）：**146 passed**
  （Phase 7 新增 42 條 = `test_application_kit.py` 35 條［生成函式、工具層、
  graph 四情境、API 整合、2 條審查回歸］ + `test_application_kit_logic.py`
  7 條［kit schema 與 prompt builder 純單元］）
- `ruff check .`、`mypy app --ignore-missing-imports`：綠
- migration 往返 `0009 → 0008 → 0009`：綠；`psql \d` 確認 unique constraint
  `uq_generated_artifacts_pair_kind_version` 存在
- 前端 `npm run lint`、`npm run typecheck`、`vitest run`：**26 passed**
  （新增 9 條：ApplicationKitPage 6 條［空狀態與 pending、三卡渲染、partial
  警示、GET 失敗、outline 原樣編輯、編輯存新版］ + export 純函式 3 條）
- Planner 冒煙（真 key）：`gemini-3.5-flash-lite` + `bind_tools` 正確回傳
  `tool_calls`，KIT_PLANNER_SYSTEM 下首輪即選 `fetch_resume` + `compute_match`
- 真實 e2e（真 Gemini + 真 DB + LangSmith tracing）：註冊 → 上傳履歷 → 建
  職缺 → agent run → GET → PATCH 全通。**三類 artifacts 齊全**（3 sections
  含 bullet rewrites / intro + 2 段 body + closing / 4 題涵蓋四類）、
  `missing=[]`、match_score 0.63、耗時 10.8~91.9 秒（步數依 LLM 決策浮動，
  皆在 240 秒 deadline 內）、編輯存出 v2（`source="edit"`）
- Docker：`--build` 重建後 `/health` 回 `{"status":"ok","db":"ok",
  "pgvector":"ok"}`、`POST /auth/register` 回 201
- 對抗性審查（5 維度 reviewer × 逐項反駁驗證，18 agents）：13 發現 →
  12 證實（去重後 6 個根因 = 問題 6~11，全數修復）、1 反駁：「planner 兩次
  都失敗時未寫 `LLMCallLog`」——程式碼觀察正確但屬設計行為（規劃書的 planner
  spec 明文只在成功路徑記帳，`test_graph_planner_failure_degrades` 已固化）；
  其附帶的「`attempts` 應計入重試」子主張則與 `llm_call_log.py` 既有欄位語意
  相反（該欄位定義明確排除網路層 transient retry，正是 planner 迴圈重試的性質）
