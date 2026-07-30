# Phase 6 開發筆記 — RAG Skill Gap Analysis（FR-24~30）

範圍：retrieval（pgvector top-k）→ rerank（本地 cross-encoder）→ generation
（structured output + citation）→ `SkillGapReport` 持久化與前端 tab。
另含 Phase 5 校準三修正的 retrofit（見決策 12）。

---

## 決策

### 決策 1：rerank 用本地 cross-encoder，不用 LLM rerank
`cross-encoder/ms-marco-MiniLM-L-6-v2`（sentence-transformers，~90MB）。
- **理由**：零 API 成本、確定性、CPU 上 k≤5 毫秒級；Phase 9 要對 25 組資料跑
  「rerank 前後對比」（ER-5），LLM rerank 會大量消耗額度且結果不可重現。
- **代價**：拖進 torch 依賴（見決策 9）；image 變大、CI 安裝 +1~2 分鐘。
- 失敗（模型載入、推論、分數長度不符）一律降級回向量排序並記
  `retrieval.rerank_error`（NFR-4）；`rerank_used` 讓 eval 能區分兩種報告。

### 決策 2：LLM 引用用 1-based chunk 編號，不用 UUID
Prompt 把證據編成 `[1] (section) content`，schema 收 `evidence_chunk_numbers:
list[int]`；service 層再映射回 chunk UUID。36 字元 UUID 要 LLM 回抄容易錯位
且浪費 token；整數 1..k 的越界驗證是一行判斷。

### 決策 3：citation 驗證後零證據的 gap 整條丟棄
`validate_analysis` 剔除越界編號後，一條 gap 若一個有效引用都不剩就整條丟棄，
計入 `dropped_gap_count`（可觀測、不靜默）。FR-28 要求每條 gap 指得到 source
——保留無證據的 gap 等於把違規推給 UI。philosophy 同 Phase 5 的
`apply_equivalences`：schema 擋格式、service 層擋語意。

### 決策 4：`TOP_K = 5`，模組常數不進 settings
職缺典型 4~8 chunks：5 覆蓋多數職缺、對長職缺仍有實際篩選，同時就是 rerank
候選數。放 `retrieval.py` 模組常數（同 `match_scoring.WEIGHTS` 的理由：Phase 9
eval 是調整它的回饋迴路，不得隨部署環境漂移）。不排除任何 section——k=5 下
不相關的 overview 自然沉底，硬排除會失去職稱／領域級 gap 的證據。

### 決策 5：檢索 query 直接複用 `ResumeEmbedding` 的 skills 向量
Phase 5 存履歷向量時就用 `task_type="RETRIEVAL_QUERY"`，skills 向量天生是
查詢側——零額外 embedding 呼叫。缺漏時 lazy backfill（同 match_service），
fallback 順序 skills → summary → experience；全無 → 409。cross-encoder 的
query 文字用同 kind 的 embedding 文字，讓兩段檢索看到同一個查詢。

### 決策 6：職缺未索引 → 409（與 Phase 5 決策 17 刻意分歧）
Match 裡 embedding 只是可歸一化的成分之一，未索引照算分；RAG 裡檢索就是
本體：無向量 = 無證據 = 無 citation（驗收條件）。回 409 並提示重新加入職缺
（目前無 re-index endpoint，Phase 10 硬化再議）。

### 決策 7：生成失敗照存報告、回 200（鏡像 MatchResult 降級對）
檢索與 rerank 是已付費的確定性結果，永遠落地（`retrieval` JSONB not null）；
LLM 產物 `analysis` nullable + `generation_error` 記原因。前端黃 banner
「generation failed — evidence still valid」+ 純證據清單。

### 決策 8：JSONB 欄位命名 `analysis` 而非 `gaps`
Payload 是 `{gaps, overall_summary, dropped_gap_count}` 整包——欄位叫 `gaps`
會出現 `gaps.gaps` 的巢狀。與 `generation_error` 構成降級對。

### 決策 9：torch CPU-only 用「兩段式安裝」，不用 extra-index-url
Linux 裸裝 torch 會拉 CUDA 版（~2GB+）。初版用 `[tool.uv.pip]
extra-index-url` 一份設定管兩邊，但審查實測推翻（見問題 3）——改為
Dockerfile 與 CI 各自先跑
`uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu`
（官方標準指令，該 index 只作用於這一步），再從 PyPI 裝其餘依賴；torch 已
滿足需求不會被重解析。代價是 Dockerfile 與 ci.yml 兩處要同步維護，以
正確性換。macOS 本地無 CUDA wheel 問題，直接裝即可。

### 決策 10：HF 模型快取放 `/opt/hf-cache`，不放 `/app`
docker-compose 以 `./backend:/app` bind mount——快取放 `/app` 內，build 期
預下載的模型會在 runtime 被 mount 蓋掉。`ENV HF_HOME=/opt/hf-cache` +
build 期 `RUN python -c "...CrossEncoder(...)"` 預下載，runtime 離線可用。

### 決策 11：reranker 走 router 層 DI（`get_reranker()`）
同 `get_llm_provider` 的注入慣例；測試以 `dependency_overrides` 換假 reranker
（`tests/test_skill_gaps.py` 設 autouse fixture 預設注入 NoopReranker），
**CI 從不下載 cross-encoder 模型**。`rerank.py` 內 `sentence_transformers`
為函式內 lazy import + 模組級單例：app 啟動與測試不付 torch import 代價。

### 決策 12：Phase 5 校準三修正在本 phase retrofit
phase5_notes 問題 5~7 的修正（實測數據都寫進筆記了）不在 code 裡——疑似
改動未 commit 即遺失。本 phase 補齊：
1. `experience_alignment` = 0.25×years + 0.75×title（單邊缺失用可用邊）
2. 新增 `job_required_skills()`：required 空時回退 qualifications
3. rescale 視窗 [0.35, 0.95] → [0.50, 0.85]

測試常數同步重算：`_STRONG_SCORE` 0.80 → **0.75**、`_WEAK_SCORE` 0.41/0.9 →
**0.38/0.9**、`_NO_EMBEDDING_SCORE` 不變（title 缺 → alignment = years 單邊）。
排序不受影響；Phase 7 的 0.5 / 0.8 路由門檻依筆記實測仍正確分流。

### 決策 13：pair 查詢 GET 與 plan 的報告 GET 並存
plan 只列 `GET /skill-gaps/{id}`，但前端進 Job Detail 頁只知道 job_id。
加 `GET /jobs/{id}/skill-gap?resume_id=`（404 = 尚未分析 → 前端顯示 Analyze
按鈕；吞 404 回 null 同 `fetchJob` 模式），報告 id GET 依 plan 保留。
Router 無 prefix（兩類路徑分屬 `/jobs/...` 與 `/skill-gaps/...`）。

### 決策 14：MatchResult missing skills 只在同履歷版本時當 hint
存在同 (resume, job) 的 MatchResult 且 `resume_version_id` 相同 → breakdown
的 missing_required + missing_preferred 進 prompt 當提示（明令逐條對照證據）；
版本不同就不給（舊版履歷的 missing 對新版是雜訊）。citation 驗證仍是硬防線。

### 決策 15：前端 tab 用本地 state、citation 用 click 展開
- Tab 不用 nested route：`App.tsx` 的 `path="*"` catch-all 會吃掉未註冊子路徑，
  且全站無 URL 狀態先例。
- plan 寫「hover 顯示原文」，但全站無 tooltip 先例；click 展開複用 Dashboard
  的單開 accordion 模式（`expandedCitation` 單一 key），效果等價且可用既有
  fireEvent 測試風格覆蓋。
- 證據按鈕以 section 名稱標示（六種 section 與「Parsed sections」卡片標題
  一一對應）。

---

## 問題

### 問題 1：本機驗證時 migration 撞「表已存在」
先跑了 pytest（conftest 的 `create_all` 在開發庫建了 `skill_gap_reports`），
再跑 `alembic upgrade head` 就撞 DuplicateTable。**這不是 migration bug**：
CI 順序是先 `alembic upgrade head` 再 pytest。本機 DROP TABLE 後往返驗證
（0007 → 0008 → 0007 → 0008）通過。教訓：本機驗證新 migration 要在 pytest
之前跑，或先清掉 create_all 產物。

### 問題 2：測試幾何要避開同分平手
正交基底下「未命中」的 chunks cosine 全為 0，pgvector 的同分排序不保證穩定。
gap 類測試改用 2-chunk job（overview 欄位全空 → 略過，只剩
responsibilities(b0) / required_skills(b1)），query=skills(b1) → 排序完全確定；
top-k / rerank 測試用 6-chunk job 但只斷言 tie-safe 事實（首位、數量、歸屬）。

### 問題 3：`extra-index-url` + uv first-index 策略會把 HTTP stack 靜默釘在 2022 年版本
初版把 PyTorch CPU index 設成 `[tool.uv.pip] extra-index-url`，假設「其餘
套件回落 PyPI」。對抗性審查實測（`uv pip compile` 於 linux/py311）推翻：
該 index 同時掛著 requests 2.28.1、urllib3 1.26.13、certifi 2022.12.7、
charset-normalizer 2.1.1 等，而 uv 預設 `first-index` 策略對「第一個含該
套件名的 index」就地解析、不回落——整條 HTTP stack 被靜默釘在帶已知 CVE
（CVE-2023-32681、CVE-2023-43804 等）與過期 CA bundle 的版本，且安裝成功、
測試全綠，完全無症狀。**修正**：改兩段式安裝（決策 9），torch 的依賴樹不含
HTTP stack，requests 等從 PyPI 取最新。教訓：把大型專案的專用 index 掛成
extra index 前，先確認上面還掛了什麼。

### 問題 4：模型預載層排在 `COPY app/` 之後，改 code 就重抓 280MB
初版 Dockerfile 沿用既有順序（COPY pyproject → COPY app/ → install →
預載模型），任何程式碼變動都讓依賴層（torch ~190MB）與模型層（~90MB）
cache 全部失效。**修正**：依賴與模型層只綁 pyproject.toml（`uv pip install
-r pyproject.toml --extra dev` 只裝依賴不 build 專案），`COPY app/` 與
`--no-deps -e .` 移到最後——改 code 只重建秒級的兩層。

### 問題 5：前端三個載入競態（審查發現，均已修）
1. `/resumes/current` 先於 `/jobs/{id}` 回應時，report effect 過早把
   `loadingReport` 關掉 → GET 飛行中顯示「No analysis yet」假空狀態。
   修正：真正發請求前 `setLoadingReport(true)`。
2. 初始 GET 無過期防護，晚到的 404 會把剛 POST 完的報告抹掉。修正：
   effect cleanup 設 ignore flag + Analyze 按鈕在 `loadingReport` 期間 disable。
3. `fetchCurrentResume` 的非 404 錯誤被折疊成「沒有履歷」，誤導使用者去
   重新上傳。修正：另立 `resumeError` 狀態顯示載入失敗。

---

## 驗證紀錄（2026-07-28）

- `pytest tests/ -q`（Docker 容器內、真 Postgres）：**104 passed**
  （新增 9 條 skill gap 整合 + 5 條純單元；校準後常數全數通過）
- `ruff check .`、`mypy app --ignore-missing-imports`：綠
- migration 往返 `0008 → 0007 → 0008`：綠
- 前端 `npm run lint`、`npm run typecheck`、`vitest run`：**17 passed**
  （新增 JobDetailPage 5 條：tab 切換、citation 展開、降級 banner、履歷載入
  失敗訊息、pending 態）
- Docker image：rebuild 成功；容器內驗證 `torch 2.13.0+cpu`、
  `HF_HUB_OFFLINE=1` 下 cross-encoder 照常載入、真模型 rerank 分數合理
  （相關 chunk -2.69 vs 無關 -11.25）
- 對抗性審查（3 reviewer × 逐項反駁驗證）：8 發現 → 5 證實（問題 3~5，
  全數修復）、3 反駁（upsert race = 文件化慣例、HNSW under-retrieval 實測
  不可達、rerank_model 常數無行為差異）
