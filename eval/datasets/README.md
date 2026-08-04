# Eval Dataset — 標註指南與設計理由（ER-4）

本目錄是 evaluation ground truth 的 **source of truth**（本地 JSON 為準，
LangSmith datasets 只是 `eval/langsmith/sync_datasets.py` 同步出去的副本）。

## 產生方式聲明

25 組情境由 LLM（Claude）輔助起草、依本指南人工審核標註而成；情境內容
（履歷、職缺）為合成資料，不含真實個資。job `parsed` 欄位是**凍結的結構化
輸入**——eval seeding 直接寫入 DB、略過 LLM 解析，讓指標不受解析非決定性
干擾；`raw_text` 僅供存檔與人工閱讀。

## Schema

一個情境一個檔：`scenarios/sNN_<slug>.json`，欄位定義與一致性驗證見
[`schema.py`](schema.py)（`eval/tests/test_dataset_schema.py` 逐檔強制執行）。
每情境固定 8 個 job（P@5 在 8 個候選下仍有實際篩選力）。

## 標註指南

### Ranking grade（`ground_truth_ranking`，0-3）

| Grade | 定義 | 例 |
|---|---|---|
| 3 | 強匹配：職缺家族相同、核心技能大多具備、資歷落點合理，是本組的「正確答案」 | 後端履歷 vs 同棧後端職缺 |
| 2 | 好匹配：職缺家族相同或緊鄰、多數要求可勝任，值得投遞 | 語意等價但用詞不同的職缺、全端 vs 前端 |
| 1 | 邊緣：有真實技能重疊但核心棧或職務家族不同，勉強可談 | Python 後端 vs Spark 資料工程 |
| 0 | 無關：職務家族不同且重疊技能非核心（**關鍵字重疊不算數**） | 招募職缺列了一堆技術名詞 |

- **二值化門檻**：P@K / MRR 以 grade ≥ 2（`schema.RELEVANT_GRADE`）視為
  relevant。取 2 不取 1：grade 1 定義上是「勉強」，把它算 relevant 會讓
  指標獎勵平庸推薦。graded 標註保留未來算 NDCG 的空間，不需重標。
- 每情境至少一個 grade ≥ 2（MRR 需要可找到的目標；schema 強制）。

### Chunk 標註（`skill_gap.relevant_chunks`，binary）

只對 skill-gap 目標 job 標（plan.md：chunk 級標註僅針對 skill-gap queries）。

**定義**：一個 chunk 是 relevant，若「一份正確的 skill gap 分析必須引用它
才能把缺口說清楚」。實務上通常是 `required_skills`、`preferred_skills`、
`experience_requirements` 這幾塊；`overview`（公司/地點）幾乎永遠不是。

- 標註前先跑 `python -m eval.datasets.show_chunks sNN` 對照 index 與內容。
- index 綁 `chunk_job(parsed)` 的決定性輸出；改動 parsed 內容會讓
  schema 測試把越界標註打爆（刻意設計：大聲失敗，不默默毀 ground truth）。

### `expected_gaps` / `must_not_claim`

- `expected_gaps`：人工判定「真實存在」的缺口與嚴重度，供標註者互查與
  judge prompt 上下文；**不是**逐字比對的標準答案（LLM 措辭可異）。
- `must_not_claim`：履歷**明顯具備**的技能清單。生成的 gap 若宣稱其中任一
  項為缺口，即為免 judge 的標註幻覺（字串正規化比對即可判定），與
  LLM-as-judge 的 hallucination rate 互為獨立訊號。

## 設計理由：兩大案例族（為什麼 ground truth 刻意包含它們）

Hybrid matcher 對 TF-IDF baseline 的優勢**只在字面與語意脫鉤時**才可觀測。
若 dataset 全是「關鍵字剛好重疊」的常規案例，TF-IDF 也能拿高分，比較就
失去鑑別力。因此至少 8 個情境各含：

1. **semantic-no-keyword-overlap**（語意匹配、零關鍵字重疊，grade ≥ 2）：
   職缺以泛稱描述同一件事（如 s01/j2 的 "a modern dynamic server-side
   language" vs 履歷的 "Python"）。TF-IDF 無訊號、embedding 應可辨識——
   量測 hybrid 的**上行**優勢。
2. **keyword-trap**（關鍵字重疊、實際無關，grade 0）：職缺堆滿履歷的技術
   名詞但職務家族完全不同（如 s01/j3 的技術獵頭、s02/j3 的手動 QA）。
   TF-IDF 會過度排前——量測 hybrid 的**抗噪**優勢。

逐情境的構造說明寫在各檔 `rationale` 欄位；census 表如下。

## 案例族 census 表

25 組情境；**semantic-no-keyword-overlap × 11、keyword-trap × 11**（皆 ≥ 8，
由 `eval/tests/test_dataset_schema.py::test_dataset_size_and_case_family_census`
強制）。逐情境構造理由見各檔 `rationale` 欄位。

| scenario | tags | title |
|---|---|---|
| s01 | semantic-no-keyword-overlap, keyword-trap | Mid-level Python backend engineer |
| s02 | semantic-no-keyword-overlap, keyword-trap | Mid-level React/TypeScript frontend engineer |
| s03 | keyword-trap, junior | Junior data analyst (SQL / Excel / Tableau) |
| s04 | semantic-no-keyword-overlap, senior | Senior DevOps / SRE engineer (Kubernetes / Terraform / AWS) |
| s05 | semantic-no-keyword-overlap | Mid-level iOS engineer (Swift / SwiftUI / UIKit) |
| s06 | keyword-trap | Mid-senior data scientist vs ML content-marketing keyword trap |
| s07 | non-tech, keyword-trap | Digital marketing manager vs affiliate ad-ops keyword trap |
| s08 | non-tech, semantic-no-keyword-overlap | Mid-level UX/UI product designer |
| s09 | career-changer, junior | Career-changer junior frontend engineer (math teacher to React) |
| s10 | keyword-trap | QA automation engineer vs manual-QA keyword trap |
| s11 | semantic-no-keyword-overlap, senior | Senior Java backend engineer with semantic-overlap probe |
| s12 | non-tech, keyword-trap | Mid-senior B2B SaaS product manager |
| s13 | semantic-no-keyword-overlap | Full-stack MERN developer |
| s14 | cross-domain | Embedded firmware engineer |
| s15 | keyword-trap | Mid-level data engineer (Spark/Airflow/dbt/Snowflake) |
| s16 | non-tech, semantic-no-keyword-overlap | Senior technical writer (API docs, docs-as-code) |
| s17 | cross-domain, keyword-trap | Senior MLOps / ML platform engineer |
| s18 | non-tech | Staff accountant moving toward FP&A financial analyst |
| s19 | non-tech, keyword-trap | B2B SaaS customer success manager |
| s20 | semantic-no-keyword-overlap, senior | Senior security engineer (SIEM / IR / cloud) |
| s21 | junior, keyword-trap | CS new grad software engineer |
| s22 | semantic-no-keyword-overlap | Android engineer (Kotlin / Jetpack Compose) |
| s23 | cross-domain | Unity game developer (C# / shaders / gameplay) |
| s24 | senior, semantic-no-keyword-overlap | Senior cloud solutions architect |
| s25 | career-changer, cross-domain | Registered nurse transitioning to healthcare data analyst |
