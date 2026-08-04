# Phase 8 實作筆記 — Evaluation Layer

> 記錄 Phase 8（評估層）實作過程中的重要決策與踩過的坑。
> 產出物：`eval/` 全套評估框架（~30 檔）、25 組標註情境、`docs/eval_report.md`。
> 執行環境限制：Gemini API **free tier**（這個限制形塑了近半數的設計決策）。

---

## 1. 重要決策

### 1.1 Eval 驅動真實 production 服務，不重新實作邏輯

**決策**：所有指標都來自呼叫真實的 `run_matches`、`run_skill_gap`、`CrossEncoderReranker`、`generate_tailored_resume_payload`，eval 只提供輸入與量測。

**為什麼**：如果 eval 自己重新實作一份「類似的」matching 或 RAG 邏輯，量到的是複製品的品質，不是使用者實際體驗的品質；而且兩份邏輯會逐漸漂移，eval 數字失去意義。唯二的 eval 專屬接縫是：(1) 透明的磁碟快取 provider decorator（服務無感）、(2) seeding 時略過 LLM 解析（見 1.4）。

### 1.2 獨立資料庫 `careerpilot_eval`，每情境一個 user

**決策**：不共用開發用的 `careerpilot` DB，也不用 ephemeral container；每個情境建立一個專屬 user（`eval+s01@careerpilot.local`）。

**為什麼**：
- `backend/tests/conftest.py` 在每個測試後 TRUNCATE 全部資料表——只要有人跑一次 pytest，共用 DB 裡的 eval 資料就全毀。
- 不用 ephemeral container 是因為 free tier 的 judge 額度迫使評估**跨日分批跑**，資料必須留存。
- 每情境一 user 讓 `--reseed s01` 只需刪 user，FK CASCADE 自動帶走該情境所有資料；而 `llm_call_logs.user_id` 是 SET NULL，成本帳本得以保留、跨日累積。

### 1.3 磁碟快取 `CachingProvider`（free tier 的命脈）

**決策**：所有 LLM/embedding 呼叫走 sha256-key 磁碟快取；只快取成功結果；cache hit 回傳的 `model` 帶 `+cached` 後綴、usage 歸零。

**為什麼**：
- Free tier 額度下「重跑」必須是零成本操作，否則任何一次中途失敗都等於燒掉一天的額度。實測價值：matching suite 中途 crash 一次，重跑時 16 次已完成的呼叫全走快取、零重複計費。
- 快取也凍結了 LLM 呼叫的非決定性——重跑數字完全重現（要量 run-to-run 變異可用 `--no-cache`）。
- `+cached` 後綴是「LLMCallLog 汙染防治」：服務照常記帳，但所有統計 SQL 過濾 `model NOT LIKE '%+cached'`，回放的結果不會混進真實 API 行為的統計。

### 1.4 Seeding 略過 LLM 解析，但走真實 embedding 路徑

**決策**：resume/job 的 `parsed` 結構是 dataset 凍結的輸入，直接寫 DB；embedding 則呼叫真實的 `generate_resume_embeddings` 與新拆出的 `create_job_from_parsed`。

**為什麼**：ground truth 標註（ranking 分級、chunk 相關性）是針對**特定的 parsed 內容**做的。如果每次 seed 都讓 LLM 重新解析原文，解析結果一變，標註就默默失效。凍結 parsed、只走真實的 chunk→embed→persist 路徑，才能同時保住「標註穩定」與「量測真實管線」。為此從 `create_job_from_text` 拆出 `create_job_from_parsed`（原函式改為委派，Phase 10 的 seed script 也能重用）。

### 1.5 Ranking 標註用 graded 0-3，chunk 標註用 binary

**決策**：每個 job 標 0-3 分（3=強匹配、2=好、1=邊緣、0=無關），P@K/MRR 以 grade≥2 二值化；chunk 相關性維持 binary。

**為什麼**：二值標註丟失「trap job 排第 2 名 vs 墊底」的資訊。保留分級讓後來加入 NDCG 成為可能（見問題 2.10——這個決策後來救了 matching suite 的鑑別力）。chunk 層級只有 5-6 個 chunk，分級的邊際價值低、標註成本高，維持 binary。

### 1.6 資料集：25 組英文情境、兩大對抗案例族

**決策**：由 Claude 生成 25 組英文情境（使用者確認），每組 8 個 job；設計兩個案例族各 ≥8 組：「語意匹配零關鍵字重疊」與「關鍵字重疊但實際無關（keyword-trap）」。

**為什麼**：
- 英文：TF-IDF 斷詞與 ms-marco cross-encoder 都對英文最有效，中文會讓 baseline 與 reranker 雙雙失真。
- 兩大案例族是 hybrid matcher 理論上贏過純關鍵字 baseline 的地方——資料集必須刻意覆蓋這些案例，對比才有意義。每組情境附 `rationale` 欄位記錄設計理由，彙整進 `eval/datasets/README.md`。
- chunk 標註用 `(job_key, chunk_index)` 而非 DB UUID：`chunk_job()` 是 parsed 的純決定性函數，dataset 驗證測試會重跑它檢查標註不越界——未來有人改 chunking 邏輯，CI 會大聲炸掉，而不是默默毀掉 ground truth。

### 1.7 TF-IDF baseline 的公平性設計

**決策**：sklearn TfidfVectorizer **per-scenario fit**；resume 文本用 production 的 `build_resume_embedding_texts`、job 文本用 `chunk_job` 內容。

**為什麼**：兩系統必須看**同一份文字基底**，否則量到的是輸入差異不是演算法差異。Per-scenario fit 防止跨情境詞彙洩漏（讓 baseline 拿到它不該有的全域資訊）。Baseline 的公平性直接決定「improvement %」數字的可信度。

### 1.8 LLM-as-judge：gemini-3.6-flash、無 fallback、一份報告一次呼叫

**決策**：judge 用獨立的 `GeminiProvider(model="gemini-3.6-flash", fallback_model="")`；一份 skill-gap 報告的所有 claims 批次進單一 prompt。

**為什麼**：
- Judge 選比 generator（3.5-flash-lite）更強的模型是 LLM-as-judge 的基本要求；不設 fallback 是因為額度是稀缺品——失敗就標 partial 隔日續，不要讓 fallback 把額度燒在重試上。
- Claims 批次化把 judge 呼叫從 ~100 次壓到 ~50 次（25 RAG + 25 no-RAG），在 free tier 下是「幾天跑完」與「一週跑完」的差別。
- 報告強制附 **self-grading caveat**：judge 與 generator 同家族，絕對數字可能偏樂觀；RAG vs no-RAG 的**對比**（同一 judge 評兩邊）才是更可信的訊號。

### 1.9 No-RAG baseline 給最寬容的證據集

**決策**：no-RAG baseline 看不到職缺內文直接生成 gap 分析；judge 評它時卻給「該 job 的**全部** chunk 原文」當證據。

**為什麼**：這個不對稱**偏向低估 RAG 的改善**——no-RAG 的宣稱只要在職缺任何地方找得到依據就算 supported。既然偏差方向對 RAG 不利，量到的差距（0% vs 44.1%）就是保守下界，結論更可信。這是刻意的實驗設計，寫進報告 note。

### 1.10 Client-side 節流，且節流時間不汙染延遲指標

**決策**：生成呼叫間隔 6.5s、judge 10s、embedding per-text 0.7s；所有節流睡眠累計於 `paced_seconds`，`Timings.timed` 計時時扣除。

**為什麼**：
- Free tier 靠 429 重試硬碰上限的問題不只是慢——**重試耗盡會觸發 model fallback**，把稀缺的 3.6-flash（judge 額度！）燒在日常流量上。主動節流是在保護 judge 額度。
- 節流是額度管理，不是系統延遲。若不扣除，system suite 的 p50/p95 會把人為睡眠算進去，數字失真。同理，只有 `cache_missed=True` 的樣本才進延遲統計——warm-cache 回放的計時不代表任何真實延遲。

### 1.11 LangSmith `evaluate()` 的 target 零 LLM

**決策**：judge suites 經 `langsmith.evaluate()` 執行，但 target 函數只讀已持久化的結果（零 LLM 呼叫），judge 在 evaluator 內經快取呼叫。

**為什麼**：ER-6/ER-8 要求 traces 留在 LangSmith，但 evaluate 每次執行都會跑 target——如果 target 觸發生成，每次重跑都燒額度。把生成（本地 suites，落 DB）與評判（judge suites，讀 DB）拆開，重跑 evaluate 只是回放，快取讓 judge 也不重複計費。

### 1.12 Suite 結果落盤 + 報告合併：為跨日續跑而生

**決策**：每個 suite 的結果存 `eval/.cache/results/<suite>.json`；`render_report` 合併「本次結果優先、沒跑的退回上一份存檔」；skipped 或零結果**不落盤**。

**為什麼**：free tier 下一份完整報告注定是多天多次執行收斂出來的。合併語意讓每次執行都產出「目前最完整的報告」；「skipped 不落盤」是踩坑後加的防護（見問題 2.12）——空結果覆蓋掉前一天的成功結果，等於把已花掉的額度作廢。

### 1.13 跳過 CI nightly eval（使用者決策）

**決策**：CI 只跑不打 API 的 eval 單元測試（113 個），不建 nightly eval workflow。

**為什麼**：free tier 額度撐不起每晚全量跑；且 eval 資料集與程式碼變動頻率低，nightly 的邊際價值不高。CI 守住的是「dataset 標註完整性、指標數學、快取行為」這些純本地可驗證的東西。

---

## 2. 遇到的問題與解法

### 2.1 pydantic-settings 啟動即炸：root `.env` 的多餘欄位

**現象**：eval 一 import `app.core.config` 就 ValidationError。

**原因**：backend 的 `Settings` 原本只在 Docker 環境載入，root `.env` 裡的 `POSTGRES_*`、`VITE_*` 等欄位它不認得，而 pydantic-settings 預設 `extra="forbid"`。

**解法**：`SettingsConfigDict(..., extra="ignore")`。這也是正確語意——`.env` 本來就是多服務共用的。

### 2.2 ruff isort 與 `sys.path` bootstrap 的雞生蛋問題

**現象**：`import eval._bootstrap` 必須在 `from app...` 之前執行（它負責把 `backend/` 塞進 `sys.path`），但 ruff isort 堅持把它排到後面，CI 過不了。

**解法**：把 bootstrap 移進 `eval/__init__.py`（`from eval import _bootstrap`）。任何 `import eval.X` 都會先執行 package `__init__`，import 順序從此免疫——比在每個檔案用 `# isort: skip` 補丁乾淨得多。

### 2.3 argparse help 字串含裸 `%` 直接 crash

**現象**：`--limit` 的 help 文字含 "the % in"，argparse 做 `%`-插值時炸 `ValueError: unsupported format character`。

**解法**：help 字串裡的 `%` 寫成 `%%`。小坑，但錯誤訊息完全沒指向 help 字串，花了幾分鐘定位。

### 2.4 `python eval/run_eval.py` 直跑時 `eval/langsmith/` 遮蔽 pip 的 `langsmith`

**現象**：直跑腳本時 `import langsmith` 拿到的是我們自己的 `eval/langsmith/` 目錄，`Client` 不存在。

**原因**：`python <path>/run_eval.py` 會把腳本所在目錄（`eval/`）放進 `sys.path[0]`，其下的 `langsmith/` 就以頂層套件之名蓋掉 pip 套件。

**解法**：`run_eval.py` 開頭偵測直跑模式，把 `eval/` 從 `sys.path` 移除、插入 repo root。plan.md 的驗收指令就是直跑形式，不能繞過。

### 2.5 SQLAlchemy `DetachedInstanceError`（matching suite）

**現象**：`run_matches` 回傳的 Job ORM 物件在 session 關閉後讀 `.id` 就炸。

**原因**：service 內部 commit 會 expire ORM 屬性，session 外存取觸發 lazy load 失敗。

**解法**：把 `job.id → job_key` 的映射移進 session 存活區塊內完成，離開 session 後只操作純資料。

### 2.6 Embedding 429 連鎖失敗：free tier 配額按「批內每段文字」計（本 phase 最大的坑）

**現象**：全量 seeding 已有 1.2s/批的節流，仍在中途撞 429（`embed_content_free_tier_requests`, 100/min），且一倒全倒——後面 17 個情境全部秒敗。

**原因**（兩層）：
1. **配額計量單位誤判**：`genai.embed_content` 一次帶整批文字算「一次呼叫」，但 free tier 配額把**批內每段文字各計一次**。一份履歷一批 ~10 段、一個 job 一批 6 chunks，實際速率 ~300 請求/分鐘，是名目節流的 3 倍。
2. **連鎖效應**：配額窗打穿後，wrapper 的 tenacity 只重試幾秒就放棄，後續每個情境都在同一個 60 秒窗內立刻失敗。

**解法**：
- `_pace_embed(n_texts)` 改為**按批次大小等比預扣**：下一次呼叫要等 `interval × n_texts`，預設 0.7s/text（≈85/min，留重試餘裕）。
- `seed_scenarios` 遇 429 先睡 65 秒讓配額窗重置再續種，單一情境失敗不炸整輪。
- 修正後 17 個情境一次補種成功、途中零 429。

### 2.7 Embedding 的「每日」配額：s25 卡關

**現象**：修好 per-minute 節流後，s25 仍然 429——quota_id 變成 `...PerDayPerProjectPerModel-FreeTier`。

**原因**：當天累計已把每日 embedding 額度用完，這不是節流能解的。

**解法**：設計本來就支援——seeding 冪等、快取保留已完成的 embed，隔日重跑 `--only seed` 只補 s25 一個情境，成功。教訓：debug 429 時**先看 quota_id 是 PerMinute 還是 PerDay**，兩者的處置完全不同。

### 2.8 429 重試會誤觸 model fallback，燒掉 judge 額度

**現象**：生成呼叫撞 429 → retry 耗盡 → production 的 fallback 機制切到 gemini-3.6-flash——正是 judge 要用的稀缺模型。

**解法**：生成呼叫加 6.5s client-side 節流，從源頭避免 429；並讓 `Timings` 扣除節流睡眠（`paced_seconds`），不汙染延遲統計。這是「free tier 下 fallback 機制反而有害」的案例——production 的韌性設計在額度稀缺情境需要被主動抑制。

### 2.9 Judge 額度耗盡的處置：從「炸掉」到「跨日收斂」

**現象**：judge 呼叫中途 429，整個 suite 失敗。

**解法**（多輪演進）：
- `_QuotaState` + `JudgeQuotaExhausted`：額度耗盡後停止後續 judge 呼叫，已完成的照寫報告、標 partial 並註明 "judged 12/25 … re-run on a later day"。
- 快取保證已 judge 的項目永不重打——隔日重跑從斷點續評。
- 後續發現 `call_judge` 把**所有** `LLMError` 都當額度耗盡（一次壞回應就放棄整輪剩餘額度）——改為只有訊息含 429/quota/rate-limit 才判定耗盡，其他錯誤轉 `JudgeCallError` 只跳過單一情境。
- 實測三天收斂：Day1 評 12 個、Day2 RAG 側全滿+no-RAG 8 個、Day3 全部收完，零重複計費。

### 2.10 Matching 兩系統指標完全同分：P@K 的鑑別力極限

**現象**：hybrid 與 TF-IDF 的 P@3/P@5/MRR 在**每個情境都一模一樣**，improvement +0.0%——看起來像 bug。

**調查**：底層排序其實不同（例如 s01 hybrid 排 `j1,j4,j2`、TF-IDF 排 `j1,j3,j2`，TF-IDF 確實被 keyword-trap 職缺騙到第 2 名）。但 P@3 只數「前 3 名有幾個相關」——兩邊前 3 名都含同樣 2 個相關 job，位置差異被二值化吃掉。

**解法**：加入 **graded NDCG@3/@5**（利用 1.5 的 0-3 分級標註，`(2^grade−1)/log2` 折扣），個別情境的差異立刻浮現（s02：hybrid 1.000 vs TF-IDF 0.864）。最終結論仍然誠實：整體上 TF-IDF 在這組資料與 hybrid 相當（NDCG@5 0.935 vs 0.955），未達 +35% 參考目標——原因是兩系統看同一份乾淨的結構化文字、且多數情境難度不足（雙方 MRR 都是 1.0）。這寫進報告作為 measure-and-report 的結論，並指出改進方向（提高情境難度）。

### 2.11 對抗式審查找出的 6 個 bug（全數修復）

實作完成後跑了一輪多 agent 對抗式審查（4 個 finder + 逐項 verify），確認 6 個真 bug：

1. **skipped 結果覆蓋成功結果**：`run_eval.py` 對 skipped 的 suite 仍呼叫 `result.save()`，前一天的成功結果被空殼覆蓋。→ 加 `if not result.skipped: result.save(cfg)`。
2. **LangSmith auto-sync 檢查太弱**：只驗「dataset 存在」，但 dataset 可能是被 `--limit` 跑截斷過的舊版。→ 改為驗「雲端 examples 的 scenario_id 集合 ⊇ 本次要評的集合」，不足即 `sync_all()` 全量重同步。
3. **LangSmith 不可用（401/斷網）炸整輪**：judge suite 的 read/sync/evaluate 失敗會讓整個 run_eval 掛掉。→ 降級為 suite skipped + note，報告照渲染（且因修正 1 不會覆蓋舊結果）。
4. **`list_examples` 邊刪邊迭代漏刪**：langsmith 的 lazy 分頁下，邊迭代邊 delete 會位移分頁，>100 筆時漏刪。→ 先 `list(...)` 物化再逐筆刪。
5. **報告合併吃掉 skip 原因**：退回上一份存檔時，讀者不知道本次其實跳過了。→ 合併時在存檔結果上附註「This run skipped the suite (原因)」。
6. **`must_not_claim` 雙向子字串比對誤報**：`"C"` 會命中所有含 c 的技能、`"Java"` 命中 `"JavaScript"`。→ 改為正規化 token 序列完全相等（寧可漏報不誤報——這是輔助訊號，主訊號是 judge）。

### 2.12 空的 judge 結果覆蓋掉已有分數

**現象**：某輪 judge 額度在 rubric 開始前就耗盡，rubric 產出 n=0 的空結果——但它標的是 `partial` 不是 `skipped`，繞過了 2.11-1 的防護，把前一天 s01 的分數覆蓋掉了。

**解法**：hallucination/rubric 在「一個判定都沒收到」時直接標 `skipped` 並提前 return（空表不落盤、不覆蓋）。損失的分數本身還在 judge 快取裡，下次重跑瞬間找回。

### 2.13 Rubric alignment 分數異常低：查證後不是 bug

**現象**：alignment 維度平均只有 2.20（其他維度 4.0+），最初懷疑 evaluator 有 bug。

**調查**：抽查 judge 的 rationale，發現是**合法的嚴格評分**——judge 一致批評 tailored-resume 建議「只重排既有內容、沒有正面處理候選人真正缺的技能（如 AWS/K8s 缺口）」。

**結論**：保留原始分數。這是評估層產出的真實產品訊號（kit generation 的 prompt 應該把 skill-gap 報告的缺口納入建議策略），正是建 eval 的目的——**發現問題，而不是把問題調整到看不見**。

### 2.14 其他小坑

- **背景執行時看不到進度**：`print` 在 stdout 重導向下是塊緩衝，長跑任務看起來像卡死。→ 用 `python -u` 直跑，或直接查 DB 計數（`match_results`、`skill_gap_reports`）判斷進度。
- **`--with-coverage` 的 DB 安全性**：coverage 子行程跑 backend pytest（conftest 會 TRUNCATE），必須確保它不會指到 eval DB——`system.py` 明確覆寫子行程的 `DATABASE_URL` 到標準測試 DB。
- **審查 finding 的可信度**：對抗式審查有 14 個 finding 的 verify agent 因 session limit 中斷，輸出上標為 "refuted" 但實際是**未驗證**——其中兩個（coverage DB 防護、call_judge 過寬判定）事後人工複查，一個安全、一個是真 bug（見 2.9）。教訓：自動化審查的「已駁回」清單要區分「驗證後駁回」與「來不及驗證」。

---

## 3. 最終數字速覽（詳見 `docs/eval_report.md`）

| 指標 | 結果 |
|---|---|
| Hallucination rate | RAG **0.0%**（0/115） vs no-RAG **44.1%**（30/68） |
| RAG rerank P@3 | 0.613 → 0.667 |
| Rubric（25 份） | relevance 4.04 / specificity 4.20 / actionability 4.24 / alignment 2.20 |
| Matching NDCG@5 | hybrid 0.935 vs TF-IDF 0.955（keyword-trap 族群 hybrid 0.926 vs 0.917） |
| Malformed rate | 0.00%（0/450，目標 <1%） |
| Backend coverage | 94.7%（目標 ≥80%） |

三天內在 free tier 額度下收斂完成；重跑 `python eval/run_eval.py` 全走快取、數字完全重現。
