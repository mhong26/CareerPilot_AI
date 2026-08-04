# Phase 5 實作筆記 — Match Ranking + Explanation

Phase 5（FR-19~23）的重要決策與實作過程遇到的問題整理。

這個 phase 第一次把「履歷」與「職缺」兩條先前各自獨立的資料線接起來：一份履歷 × 多個職缺
→ 混合演算法算 `match_score` → 排序 → LLM 生成結構化解釋 → 持久化。

---

# 一、重要決策

## 1.1 產品流程

### 決策 1：Explanation 在 `POST /matches/run` 內**同步**生成
- **選項**：(a) run 時一次把分數 + explanation 全算完；(b) run 只算分數，使用者展開某筆時才生成。
- **選擇 (a)**，理由：API 面最單純（一個 endpoint、一種狀態），符合 plan.md「批次計算存 MatchResult」
  的原文，前端只要一個 loading 狀態。代價是 5 個 job 要等 45~50 秒（實測），可接受。
- **代價已知**：每個 job 最多 2 次 LLM 呼叫（技能等價 + explanation），對免費層額度壓力大
  （見問題 2）。`job_ids` 因此設 `max_length=50` 上限。

### 決策 2：重跑**覆蓋**而非保留歷史
- 每組 `(resume_id, job_id)` 只留最新一筆（unique constraint + upsert），`updated_at` 即「上次執行時間」。
- **理由**：Dashboard 永遠只需要最新結果，保留歷史會讓查詢要 group by、資料無限累積，
  而本 phase 前端也不做歷史比較 UI。FR-23「日後查看」仍然滿足。

### 決策 3：前端沿用手寫 `useState`/`useEffect`，不引入 TanStack Query
- 專案雖然裝了 TanStack Query 且 plan.md 技術棧有列，但現有四個頁面全部是手寫模式。
- **理由**：一致性優先。只為 matches 引入會讓 codebase 出現兩種資料層風格，
  且既有測試都是 mock api module 的寫法，引入後要另外加 `QueryClientProvider` wrapper。

## 1.2 演算法

### 決策 4：Hybrid 混合計分，而非單一 embedding 相似度
```
match_score = Σ(wᵢ × sᵢ) / Σ(wᵢ)      # 只對「可用」成分求和
WEIGHTS = { embedding 0.35, required 0.35, preferred 0.10, experience 0.20 }
```
- **為什麼不能只用 embedding**：(a) 鈍感——兩份都是軟體工程師的履歷對同一職缺 cosine 可能只差
  0.02；(b) 看不見硬性條件——職缺要 Kubernetes 而履歷完全沒有，但整體「氛圍」仍像後端工程師，
  分數照樣高；(c) 無法解釋——一個 0.83 的數字沒辦法告訴使用者為什麼。

### 決策 5：成分缺失時**權重歸一化**，不給中性分
- 職缺沒列 preferred skills → `preferred_coverage = None` → 把 0.10 權重拿掉、其餘按比例放大。
- **理由**：硬給 0.5 會汙染排序（沒列 preferred 的職缺不該因此被拉向中間）。歸一化後分數
  仍落在 [0,1] 且語意誠實。實際使用的權重存進 `breakdown.weights_used` 供稽核。
- **但這個設計有反效果的邊界情況**，見問題 6。

### 決策 6：技能比對只做「完全相等」，不做子字串
- `normalize_skill` 正規化（小寫、去標點、保留 `c++` / `c#` / `node.js`）後只比完全相等。
- **理由**：子字串比對會讓 `"java"` 匹配到 `"javascript"`——這是最典型的假陽性陷阱。
  對不上的交給 LLM 語意等價層處理，寧可漏判也不要誤判。

### 決策 7：LLM 語意等價採「先便宜、後花錢」兩層，且回傳結果必須驗證
- 第一層正規化字串比對（免費、確定性、可單元測試）；只有對不上的才丟給 Gemini
  問等價（如 `Go` ≈ `Golang`），每對 resume×job 至多**一次** batch 呼叫。
- **回傳的 pair 一律驗證**：`job_skill` 必須真的在未匹配集合、`resume_skill` 必須真的在履歷
  技能池，否則丟棄（`apply_equivalences`）。這是幻覺防護——不能讓模型憑空宣稱使用者會某個技能。
- LLM 呼叫失敗 → 只用 exact 結果繼續，不 crash。

### 決策 8：Explanation 讓 LLM「轉述」而非「判斷」
- **不是**把履歷與職缺原文丟給 LLM 叫它自己評估，而是把**已經算好的結構化事實**
  （各成分分數、matched/missing 技能清單、年資對比）餵進去，叫它寫成人話。
- **理由**：自由發揮會幻覺（可能宣稱使用者會某個沒寫的技能）。給算好的 diff 資料，
  LLM 的工作從「判斷」降級為「敘述」，解釋與分數保證一致、幻覺空間最小。
- 生成失敗 → `explanation = null` + `explanation_error`，**分數照存**（NFR-4）。

### 決策 9：權重與校準常數放**模組常數**，不放 settings / env var
- **理由**：(a) Phase 7 的 agent 以 0.5 / 0.8 門檻對 `match_score` 路由，公式不得隨部署環境漂移；
  (b) Phase 3 有過「docker-compose 漏傳 env var → 靜默使用預設值」的事故（phase3_notes 問題 9），
  不想再增加這類面向；(c) 這是演算法校準值，不是部署設定。
- Phase 8 的 P@K / MRR 是正式校準迴路，屆時調整是一行 diff。

## 1.3 架構

### 決策 10：純函式計分層與編排層分離
- `services/match_scoring.py`：所有確定性數學（技能正規化、覆蓋率、cosine、rescale、
  年資解析、加權合成），**零 DB / LLM 相依**。
- `services/match_service.py`：編排（載入向量、呼叫 LLM、upsert）。
- **理由**：計分邏輯可以零 fixture 單元測試、毫秒級跑完，這正是 plan.md「score 計算
  deterministic 部分」測試要求的落點。

### 決策 11：`ResumeEmbedding` FK 指向 **resume version** 而非 resume
- 每次編輯履歷產生新版本 → 新的一組向量；舊版本向量**保留**。
- **理由**：`MatchResult.resume_version_id` 記錄評分當下的履歷快照，若刪掉舊版向量，
  歷史結果就無法重現（SRS §5.3.4 要求不破壞歷史）。每版最多 3 筆 × 768 float，成本微不足道。

### 決策 12：`resume_embeddings` **不建** HNSW 向量索引
- **理由**：這些向量永遠是「按 version 取出後在 Python 端比對」（每版最多 3 筆），
  從來不是相似度掃描的目標。查詢沒有 `ORDER BY vector <=> x`，planner 也不會選它。
  建索引只會增加寫入成本。對照 `job_embeddings` 就有 HNSW——因為 Phase 6 要對它做 top-k 檢索。

### 決策 13：交易順序——所有 LLM 呼叫必須在寫入段之前完成
- `record_call`（LLM 記帳）**自帶 commit**，如果夾在半建好的實體中間會把不完整的 row 刷進 DB。
- 因此 `run_matches` 的流程刻意是：載入向量 → 全部計分 + 全部 explanation（LLM 都在這段）
  → **最後**單一 commit 寫入所有 MatchResult。
- **副作用（可接受）**：中途 crash 只會留下 log 與 embeddings，不會留半套 MatchResult。
- 這個不變式沿用自 `resume_service` / `job_service`，不是本 phase 新發明。

### 決策 14：upsert 用 select-then-update，不用 Postgres `ON CONFLICT`
- **理由**：符合 repo 既有的純 ORM 風格；單一使用者無並發寫入；in-place UPDATE 讓
  `updated_at` 的語意自然（「上次執行時間」）。unique constraint 仍是 DB 層的保險。

### 決策 15：一個 migration `0007` 同時建兩張表
- `resume_embeddings` 與 `match_results` 同屬一個 phase 交付，不存在「只要其中一張」的部署狀態；
  CI 是 `alembic upgrade head` 後才跑 pytest，兩張表本來就要一起到位。

## 1.4 API

### 決策 16：`POST /matches/run` 回 **200** 而非 201
- 它是「計算並 upsert」的動作型 endpoint，重跑是原地更新而非建立新資源。
  （這點刻意偏離 repo 其他 create endpoint 的 201 慣例，已在程式碼註解說明。）

### 決策 17：逐 job 寬容失敗，而非整包 404
- 職缺不存在／非本人 → `skipped: [{job_id, reason: "not_found"}]`；解析失敗 → `"not_parsed"`。
  全部被跳過也仍回 200。
- **例外**：履歷本身找不到 → 404；履歷解析失敗／無版本 → 409（整個請求無法進行）。
- **理由**：NFR-4，單一 job 有問題不該讓整批匹配失敗。
- **注意**：「已解析但未索引」的職缺**照樣計分**，只是 embedding 成分缺失、權重歸一化。

### 決策 18：`GET /matches?resume_id=` 的 `resume_id` 必填
- 不做「未指定就用目前履歷」的隱式行為。Dashboard 本來就已經從 `GET /resumes/current`
  拿到 id 了，隱式魔法只會讓 API 行為難以預測。

---

# 二、遇到的問題

## 二之一、模型與配額（最大的意外）

### 問題 1：`gemini-2.5-pro` 免費層配額是 **0**，FR-58 的 fallback chain 形同虛設
- **發現經過**：真實驗收跑 5 個職缺時，`LLMCallLog` 顯示 `gemini-2.5-flash` 成功 18 次、
  `gemini-2.5-pro` 失敗 18 次。用一個極小的 `"Say OK"` 請求單獨測試 pro，仍然 429。
- **原因**：該專案免費層對 2.5-pro 的四個配額指標**全部是 `limit: 0`**——不是用完，是根本沒有：
  ```
  GenerateRequestsPerDayPerProjectPerModel-FreeTier        limit: 0
  GenerateRequestsPerMinutePerProjectPerModel-FreeTier     limit: 0
  GenerateContentInputTokensPerModelPerDay-FreeTier        limit: 0
  GenerateContentInputTokensPerModelPerMinute-FreeTier     limit: 0
  ```
  一開始曾誤以為定價頁寫「免費方案無須支付費用」就代表可用——**那頁講的是價格，配額是另一套
  獨立機制**，而且 Google 已不在文件公布各模型的免費層數字，只能到 AI Studio 查專案實際額度。
- **影響**：primary 一旦碰到 429，fallback 到 pro 必定失敗，還白白多等 6 秒。整個 FR-58
  的機制從未真正運作過。
- **解法**：換模型（見問題 3）。

### 問題 2：免費層每日只有 20 次請求，驗收時大量 429
- **原因**：`gemini-2.5-flash` 的免費層額度是 **RPM 5 / RPD 20**。而 Phase 5 的設計是每個 job
  最多 2 次 LLM 呼叫，跑一次 5 個職缺的匹配就要 10 次，加上履歷解析 1 次 + 職缺解析 5 次，
  單次完整驗收就吃掉 16 次——一天的額度只夠跑一次多。
- **實測結果**：26 次 `generate_structured` 操作中 13 次成功、13 次失敗。
- **附帶驗證**：這反而證明了降級路徑真的有效——explanation 生成失敗時分數照樣算完、
  照樣存檔、API 回 200，前端顯示黃色警示。這是實跑驗證，不只是測試裡的 mock。
- **解法**：改用額度大 25 倍的模型（見問題 3）。

### 問題 3：`gemini-3.5-flash` 也被限流，最後改用 `gemini-3.6-flash`
- **原因**：查 AI Studio 的實際額度後發現分水嶺是**模型等級而非世代**：
  | 模型 | RPM | RPD |
  |---|---|---|
  | `gemini-2.5-flash` / `3.5-flash` / `3.6-flash` | 5 | **20** |
  | `gemini-2.5-flash-lite` | 10 | 20 |
  | **`gemini-3.5-flash-lite` / `3.1-flash-lite`** | 15 | **500** |
  | `gemini-embedding-001` | 100 | 1000 |
- **決定的配對**：primary = `gemini-3.5-flash-lite`（500 RPD，日常流量）、
  fallback = flash 級（20 RPD，只當救援預算）。這**沒有違背 FR-58 的意圖**——fallback 本來
  就該是「更強的模型、只在 primary 失敗時觸發」，而且這一改讓 fallback chain 從裝飾品變成
  真正可運作的機制。
- **但選 fallback 時又踩一次**：先設 `gemini-3.5-flash`，實測仍持續 429；改測
  `gemini-3.6-flash` 則單次請求成功。故最終 fallback = `gemini-3.6-flash`。
- **連帶影響**：SRS ER-6 原本明文規定 LLM-as-judge 用 `gemini-2.5-pro`（額度 0，Phase 8 必撞牆），
  已一併改為 `gemini-3.6-flash`，SRS 與 plan.md 同步更新。

### 問題 4：換模型會讓成本追蹤靜默歸零
- **原因**：`pricing.py` 的價目表只有 2.5 系列。`estimate_cost` 對未知 model 回 `Decimal("0")`
  （刻意的容錯設計，不讓找不到價格導致主流程失敗）——但換成 3.x 後，FR-56 的成本追蹤
  會**無聲無息**全部記 0，而且不會有任何錯誤訊息。
- **解法**：查官方定價頁補上 `gemini-3.5-flash-lite`（$0.30 / $2.50）與
  `gemini-3.6-flash`（$1.50 / $7.50）；2.5 系列**保留**，因為歷史 `LLMCallLog` 仍參照那些 model 名。

## 二之二、計分校準（實測跑分才發現的設計缺陷）

用一份 6 年資深後端履歷（Python/FastAPI/PostgreSQL/K8s/AWS/Golang）對 5 個相關度遞減的職缺
實跑，才暴露出三個從程式碼看不出來的問題。**初版排序其實是對的**，壞掉的是分數的絕對值。

| 成分 | 權重 | 五個職缺的實際鑑別幅度 |
|---|---|---|
| required_coverage | 0.35 | **0.714** ← 最強訊號 |
| embedding | 0.35 | 0.423 |
| preferred | 0.10 | 1.000（僅 1 筆非零，稀疏）|
| experience | 0.20 | **0.166** ← 幾乎不鑑別 |
| └ years_score | — | **0.000** ← 五個全部 = 1.0 |
| └ title_similarity | — | 0.332 |

### 問題 5：`years_score` 是常數，不是訊號
- **原因**：履歷 7 年資歷，五個職缺要求 2~5 年，`min(7/required, 1)` 全部封頂在 **1.0**——
  包含那個完全無關的護理師職缺。它對排序的貢獻是零，實際作用只是替每個職缺的 experience
  分數墊高 +0.5，**把所有人的分數地板拉高**。
- **根源**：「超過門檻就滿分」的設計錯了。年資本質上是**篩選條件（不足才該扣分）**，
  不是加分項；任何資深者對任何低年資門檻的職缺都會拿滿分。
- **解法**：`experience_alignment` 從「years 與 title 等權平均」改為
  `0.25 × years + 0.75 × title_similarity`。years 仍完整記入 `breakdown` 供 explanation 引用，
  但不再主導。title similarity 才是有領域鑑別力的那半（實測幅度 0.33）。

### 問題 6：`required_coverage = None` 反而**獎勵**了最差的匹配
- **原因**：護理師職缺的要求（RN 執照、ICU 經驗、BLS/ACLS 證照）被解析器放進 `qualifications`
  而非 `required_skills`，導致 required 為空 → `coverage` 回 `None` → **最強的負面證據整個消失**，
  那 0.35 權重被重新分配到 embedding 和 experience 上。
- **實測差距**：照常給 0.0 的話分數是 0.227；給 None 變成 0.350，**憑空多了 0.12**。
  決策 5 的權重歸一化在這個邊界情況下產生了反效果。
- **解法**：新增純函式 `job_required_skills(parsed)`——`required_skills` 為空時回退用
  `qualifications`。回退後 exact 層對句子必然不中，coverage 趨近 0，正確反映不匹配；
  而真正相符的（如 `"Strong experience with Python"` ↔ `"Python"`）仍可由 LLM 等價層救回。

### 問題 7：rescale 視窗過寬，浪費一半刻度
- **原因**：初版設 `[0.35, 0.95]`，是憑經驗估的。實測原始 cosine 只落在 **0.527 ~ 0.780**
  ——完全無關的職業也有 0.53，而上限遠不及 0.95。上下各有一大段從未用到。
- **解法**：收緊為 `[0.50, 0.85]`。

**三項修正的累積效果**（實測資料重算）：

| | 完美後端 | 平台 | 資料 | 前端 | 護理師 | 幅度 |
|---|---|---|---|---|---|---|
| 初版 | 0.858 | 0.647 | 0.449 | 0.407 | **0.350** | 0.508 |
| 校準後 | 0.863 | 0.606 | 0.383 | 0.329 | **0.077** | **0.786** |

無關職缺從「35% 匹配」降到「8%」。**排序在校準前後完全相同**——修正的是分數的絕對值可信度
（35% 看起來像「有點相關」，是誤導），不是名次。Phase 7 的 0.5 / 0.8 門檻在修正前後都仍正確分流。

### 問題 8：分數跨次執行不穩定
- **現象**：同一組資料重跑，Acme 那筆的 `preferred_coverage` 從 1.0 掉到 0.5，總分 0.859 → 0.809。
- **原因**：LLM 語意等價呼叫被 rate limit 打到時，該次就少了 `Go ≈ Golang` 這類匹配，
  coverage 因而變動。這是「把 LLM 放進計分迴路」的固有代價。
- **現況**：排序不受影響，暫不處理。但 Phase 8 做 eval 時必須知道這個變異來源，
  否則 P@K / MRR 的數字會不可重現。

## 二之三、實作與測試的坑

### 問題 9：既有測試的假 provider 沒實作 `embed`，一改就全紅
- **原因**：`tests/test_resumes.py` 的 `_FakeProvider.embed` 原本是 `raise NotImplementedError`
  （Phase 3 時履歷根本不需要向量）。Phase 5 讓上傳／編輯履歷都會產生 embedding 之後，
  所有履歷測試都會 500。
- **這是規劃階段就預先標記為最高風險的項目**，因此在同一個 commit 內處理。
- **解法**：`_FakeProvider.embed` 改為回傳 768 維正交基底向量，並支援注入 `embed_error`；
  另補兩個測試——(a) embed 失敗時上傳仍 201 且不留 embedding rows、(b) 成功路徑產生
  summary/skills/experience 三筆。

### 問題 10：測試清理漏表（與 Phase 3 同款）
- **原因**：`conftest.py` 的 `TRUNCATE` 是硬編碼表名清單。
- **解法**：補上 `resume_embeddings, match_results`。（phase3_notes 問題 13 一模一樣的坑，
  這次因為事先查過筆記所以第一步就處理了。）

### 問題 11：numpy float 會讓 JSONB 寫入失敗
- **原因**：pgvector 從 ORM 讀回來的向量是 numpy array，算出的 cosine 是 `numpy.float64`。
  這種型別放進 `breakdown` dict 後，psycopg2 序列化 JSON 時會失敗。
- **解法**：在 `cosine()` 與 `compose_match_score()` 的回傳處集中做 `float()` 轉型，
  不讓 numpy 型別滲出純函式層。（規劃時就標記為陷阱，屬預防性處理。）

### 問題 12：重跑時 `updated_at` 不會前進
- **原因**：select-then-update 的 upsert 若所有欄位值都沒變，SQLAlchemy ORM 判定
  「無變更」就不會發出 UPDATE 語句——但「上次執行時間」在語意上仍應該前進。
- **解法**：寫入段一律顯式設 `row.updated_at = func.now()`，用 DB 端時鐘當單一時間來源。

### 問題 13：本機直跑後端連不上資料庫
- **原因**：`.env` 的 `DATABASE_URL` 指向 docker-compose 的服務名 `db:5432`。在本機直接
  `uvicorn` 起服務時這個 hostname 解析不了。
- **解法**：本機驗收時覆蓋成 `localhost:5432`。（測試不受影響——pytest 從 `backend/` 執行，
  該目錄沒有 `.env`，`Settings` 用的是 `localhost` 預設值。**反過來說，千萬不要在 repo
  根目錄 `export $(cat .env)` 後跑測試**，那會讓 conftest 與 settings 都指向 `db` 而全部連不上。）

### 問題 14：改公式後測試的硬編碼期望值失效
- **原因**：`test_matches.py` 用正交基底向量讓 cosine 恰為 0/1，好手算精確的組合分數，
  期望值是硬編碼常數（`_STRONG_SCORE = 0.80`）。experience 公式一改，這些數字就不對了。
- **解法**：依新公式重算並更新 docstring 裡的推導過程——
  strong `0.80 → 0.75`、weak `0.41/0.9 → 0.38/0.9`。
  `_NO_EMBEDDING_SCORE` 維持不變（該情境下 title_similarity 也缺席，
  experience 只剩 years，歸一化後仍是 1.0）。
- **這其實是好事**：手算期望值讓公式變動無法悄悄溜過測試。

---

# 一句話教訓

> 這個 phase 最大的收穫不在程式碼，而在**「只有真的跑起來才會發現的問題」**：
> 計分公式在單元測試裡全綠、排序也一直是對的，但實跑才看出 `years_score` 根本是個常數、
> 權重歸一化在邊界情況會獎勵最差的匹配、rescale 視窗浪費一半刻度——這些都是
> 「邏輯正確但校準錯誤」的問題，測試抓不到。
>
> 同樣地，免費層額度的真相也不在定價頁上（那頁只講價格），必須實際打 API 看 429 的
> `limit: 0` 才知道 fallback 模型從頭到尾就是不能用的。**遇到與文件不符的現象時，
> 直接測一次比推論可靠。**
