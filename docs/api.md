# CareerPilot AI — API Reference

本文件整理後端目前所有對外 API endpoint（來源：`backend/app/api/`）。

- **Base URL**
  - **開發**：`http://localhost:8000`（前端直連 backend，由 `VITE_API_URL` 指定，
    見 `frontend/src/api/client.ts`）
  - **正式（Phase 10）**：同源相對路徑 **`/api`**（production build 不設
    `VITE_API_URL`，client 落到預設值 `/api`）。nginx 反向代理
    （`frontend/nginx.conf`）會在轉發前**剝掉 `/api` 前綴**：瀏覽器呼叫
    `/api/auth/login` → nginx 轉發 `/auth/login` 到 `backend:8000`。
    **後端路由本身沒有 `/api` 前綴**；同源代理下也無 CORS 問題。
- **API 文件**：FastAPI 自動產生於 `/docs`（Swagger UI）與 `/openapi.json`
- **認證**：除 `/health` 與 `/auth/register|login|refresh|logout` 外，全部需要
  `Authorization: Bearer <access_token>`（`app/api/deps.py::get_current_user`）
- **資料隔離**：所有資源皆以目前使用者為範圍，查不到他人資料一律回 404
- **共同錯誤**：`401` 未帶 / 無效 access token、`422` request body 驗證失敗、
  `429` 超過 rate limit（見下）、`503` DB 連線失敗
- **Rate limits**（Phase 9，slowapi；`app/core/ratelimit.py`）：已登入請求以
  bearer token 為 key（≈ per-user），未登入以 client IP 為 key；超限回
  `429 { "detail": "Too many requests. ..." }`。預設值（除 `/auth/refresh`
  為程式內固定值外，皆定義於 `app/core/config.py`，可由環境變數覆寫）：

  | Endpoint | 限制 |
  | --- | --- |
  | `POST /auth/register`、`POST /auth/login` | 10/minute |
  | `POST /auth/refresh` | 30/minute（固定） |
  | `POST /resumes/upload`、`PATCH /resumes/{id}` | 10/minute |
  | `POST /jobs` | 20/minute |
  | `POST /matches/run` | 5/minute |
  | `POST /jobs/{id}/skill-gap` | 5/minute |
  | `POST /jobs/{id}/generate-application-kit` | 3/minute |

## Endpoint 總覽

| Method | Path | 認證 | 說明 |
| --- | --- | :---: | --- |
| GET | `/health` | – | 健康檢查（DB / pgvector） |
| POST | `/auth/register` | – | 註冊 |
| POST | `/auth/login` | – | 登入，取得 token pair |
| POST | `/auth/refresh` | – | 以 refresh token 換新 token pair（rotate） |
| POST | `/auth/logout` | – | 撤銷 refresh token |
| GET | `/auth/me` | ✓ | 目前使用者 |
| POST | `/resumes/upload` | ✓ | 上傳檔案或貼上文字 → 解析履歷 |
| GET | `/resumes/current` | ✓ | 目前使用者最新履歷 |
| GET | `/resumes/{resume_id}` | ✓ | 履歷詳情 |
| PATCH | `/resumes/{resume_id}` | ✓ | 編輯履歷（存成新版本） |
| GET | `/resumes/{resume_id}/versions` | ✓ | 履歷版本列表 |
| POST | `/jobs` | ✓ | 新增職缺（貼上原文）→ 解析 + 建索引 |
| GET | `/jobs` | ✓ | 職缺列表（輕量） |
| GET | `/jobs/{job_id}` | ✓ | 職缺詳情 |
| DELETE | `/jobs/{job_id}` | ✓ | 刪除職缺 |
| POST | `/matches/run` | ✓ | 一份履歷 × 一批職缺，批次計分 + 解釋 |
| GET | `/matches?resume_id=` | ✓ | 該履歷的匹配結果（分數 desc） |
| POST | `/jobs/{job_id}/skill-gap` | ✓ | 執行 skill gap 分析（檢索 + rerank + 生成） |
| GET | `/jobs/{job_id}/skill-gap?resume_id=` | ✓ | 讀取該 (resume, job) 的既有報告 |
| GET | `/skill-gaps/{report_id}` | ✓ | 依報告 id 讀取 |
| POST | `/jobs/{job_id}/generate-application-kit` | ✓ | 跑 kit agent，一次產出三類 artifacts |
| GET | `/jobs/{job_id}/application-kit?resume_id=` | ✓ | 讀取該 (resume, job) 各類 artifact 最新版 |
| PATCH | `/artifacts/{artifact_id}` | ✓ | 保存編輯版（append-only 出新版本） |

---

## 1. Health

### `GET /health`
無需認證。

**200**
```json
{ "status": "ok", "db": "ok", "pgvector": "ok" }
```
DB 連線失敗時回 **503**，`status` 為 `"degraded"`、`db` 為 `"error: <訊息>"`
（可供 readiness check 使用）；`pgvector` 僅為資訊性欄位，可能為
`ok` / `not_installed` / `error` / `unknown`（未安裝仍回 200）。

---

## 2. Auth（`/auth`）

### `POST /auth/register` → `201`
**Request**
```json
{ "email": "user@example.com", "password": "至少 8 碼，最多 72 bytes", "full_name": "Optional" }
```
**Response — `UserResponse`**
```json
{
  "id": "uuid", "email": "user@example.com", "full_name": "Optional",
  "is_active": true, "created_at": "2026-07-31T00:00:00Z"
}
```
**錯誤**：`409` Email already registered

### `POST /auth/login` → `200`
**Request**：`{ "email": "...", "password": "..." }`

**Response — `Token`**
```json
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```
**錯誤**：`401` Incorrect email or password

### `POST /auth/refresh` → `200`
**Request**：`{ "refresh_token": "..." }` → **Response**：`Token`（舊 refresh token 會被輪替失效）

**錯誤**：`401` Invalid or expired refresh token

### `POST /auth/logout` → `204`
**Request**：`{ "refresh_token": "..." }`（無回應 body）

### `GET /auth/me` → `200`
**Response**：`UserResponse`

---

## 3. Resumes（`/resumes`）

共同回應型別 **`ResumeResponse`**：

```json
{
  "id": "uuid",
  "source_filename": "resume.pdf | null",
  "source_type": "pdf | docx | text",
  "parse_status": "pending | success | failed",
  "parse_error": "string | null",
  "created_at": "datetime",
  "current_version_number": 1,
  "parsed_data": { "...ResumeParsed..." }
}
```

**`ResumeParsed`**（`app/ai/parsers/resume_schema.py`，欄位皆有預設值）：
```json
{
  "basic_info": { "name": "", "email": "", "phone": "", "location": "", "links": [] },
  "summary": "",
  "skills": [],
  "experience": [{ "company": "", "title": "", "start_date": "", "end_date": "", "bullets": [] }],
  "projects": [{ "name": "", "description": "", "bullets": [], "tech": [] }],
  "education": [{ "school": "", "degree": "", "field": "", "graduation": "" }],
  "certifications": []
}
```

### `POST /resumes/upload` → `201`
`multipart/form-data`，**`file` 與 `text_content` 二擇一（恰好一個）**。

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `file` | file | PDF 或 DOCX |
| `text_content` | string | 直接貼上純文字 |

**錯誤**
- `400` 兩者都給或都不給
- `413` 檔案超過 `MAX_UPLOAD_SIZE_MB`，或文字超過 `MAX_TEXT_INPUT_CHARS`
- `415` 非 PDF / DOCX
- `422` 文字擷取失敗（如空白、過短）

### `GET /resumes/current` → `200`
最新一份履歷。`404` No resume found for this user.

### `GET /resumes/{resume_id}` → `200`
`404` Resume not found.

### `PATCH /resumes/{resume_id}` → `200`
送出整份結構化履歷，存成**新版本**。

**Request**：`{ "parsed_data": { ...ResumeParsed... } }`；`404` Resume not found.

### `GET /resumes/{resume_id}/versions` → `200`
**Response — `ResumeVersionResponse[]`**（不含完整 `parsed_data`）
```json
[{ "id": "uuid", "version_number": 1, "label": "...", "created_at": "datetime" }]
```

---

## 4. Jobs（`/jobs`）

### `POST /jobs` → `201`
**Request**：`{ "raw_text": "職缺原文" }`

**Response — `JobResponse`**
```json
{
  "id": "uuid", "company": "string | null", "title": "string | null",
  "parse_status": "...", "parse_error": null,
  "index_status": "...", "index_error": null,
  "created_at": "datetime", "raw_text": "...", "chunk_count": 5,
  "parsed_data": { "...JobParsed..." }
}
```

**`JobParsed`**（`app/ai/parsers/job_schema.py`）：`company`、`title`、`location`、
`work_mode`（`remote` / `hybrid` / `onsite`）、`responsibilities[]`、
`required_skills[]`、`preferred_skills[]`、`qualifications[]`、`experience_requirements[]`。

**錯誤**：`422` 文字擷取失敗

### `GET /jobs` → `200`
**Response — `JobListItem[]`**（輕量，不含 `parsed_data` / `raw_text`）
```json
[{ "id": "uuid", "company": null, "title": null,
   "parse_status": "...", "index_status": "...", "created_at": "datetime" }]
```

### `GET /jobs/{job_id}` → `200`
`JobResponse`；`404` Job not found.

### `DELETE /jobs/{job_id}` → `204`
`404` Job not found.

---

## 5. Matches（`/matches`）

### `POST /matches/run` → `200`
批次計分並 upsert（重跑覆蓋既有結果，故回 200 而非 201）。

**Request**
```json
{ "resume_id": "uuid", "job_ids": ["uuid", "..."] }
```
`job_ids` 長度 1–50（同步計算，每個 job 最多 2 次 LLM 呼叫）。

**Response — `MatchRunResponse`**
```json
{
  "resume_id": "uuid",
  "resume_version_number": 1,
  "results": [ /* MatchResultItem，依 match_score desc */ ],
  "skipped": [{ "job_id": "uuid", "reason": "not_found | not_parsed" }]
}
```

**`MatchResultItem`**
```json
{
  "id": "uuid", "job_id": "uuid", "job_title": null, "job_company": null,
  "match_score": 0.0,
  "breakdown": {
    "embedding_similarity": null, "required_coverage": null,
    "preferred_coverage": null, "experience_alignment": null,
    "matched_required": [], "missing_required": [],
    "matched_preferred": [], "missing_preferred": [],
    "equivalent_pairs": [{ "job_skill": "Go", "resume_skill": "Golang" }],
    "llm_equivalence_used": false,
    "resume_years": null, "required_years": null, "years_score": null,
    "title_similarity": null, "weights_used": {}
  },
  "explanation": {
    "why_matched": "", "top_overlap": [], "missing_skills": [], "risks": []
  },
  "explanation_error": null,
  "created_at": "datetime", "updated_at": "datetime"
}
```
> `breakdown` 內某成分為 `null` 代表該成分**不可用**（權重已重新歸一化，見 `weights_used`），不是 0 分。
> `updated_at` = 上次執行時間。

**錯誤**
- `404` Resume not found.
- `409` Resume has no parsed version to match against.

### `GET /matches?resume_id={uuid}` → `200`
**Response**：`MatchResultItem[]`（`match_score` desc）；`404` Resume not found.

---

## 6. Skill Gaps

路由分屬兩個入口：以職缺為入口的動作與 pair 查詢（`/jobs/...`）、以報告為入口的讀取（`/skill-gaps/...`）。

共同回應 **`SkillGapReportResponse`**
```json
{
  "id": "uuid", "resume_id": "uuid", "resume_version_number": 1, "job_id": "uuid",
  "retrieval": {
    "query_kind": "", "top_k": 0,
    "chunks": [{ "chunk_id": "uuid", "chunk_index": 0, "section": "",
                 "cosine_similarity": 0.0, "rerank_score": null }],
    "ranked_chunk_ids": ["uuid"],
    "rerank_used": false, "rerank_model": null, "rerank_error": null
  },
  "analysis": {
    "gaps": [{ "skill": "", "severity": "high | medium | low", "reason": "",
               "evidence_chunk_ids": ["uuid"], "suggestion": "" }],
    "overall_summary": "",
    "dropped_gap_count": 0
  },
  "generation_error": null,
  "chunks": [{ "id": "uuid", "section": "", "content": "原文" }],
  "created_at": "datetime", "updated_at": "datetime"
}
```
> `retrieval.chunks` 為向量（rerank 前）序、`ranked_chunk_ids` 為 rerank 後序；
> 回應中的 `chunks`（含原文，供前端展開 citation 免二次請求）按 rerank 後順序。
> `dropped_gap_count` = citation 驗證後因零證據被丟棄的 gap 數。

### `POST /jobs/{job_id}/skill-gap` → `200`
執行檢索 + rerank + 生成並 upsert（重跑覆蓋既有報告，故回 200）。

**Request**：`{ "resume_id": "uuid" }`

**錯誤**
- `404` Resume not found. / Job not found.
- `409` Resume has no parsed version to analyze.
- `409` Resume embeddings are unavailable for retrieval.
- `409` Job is not indexed for retrieval. Re-add the job to rebuild its index.

### `GET /jobs/{job_id}/skill-gap?resume_id={uuid}` → `200`
讀取該 (resume, job) 的既有報告。`404` = 尚未分析 / Resume not found / Job not found。

### `GET /skill-gaps/{report_id}` → `200`
依報告 id 讀取。`404` Skill gap report not found.

---

## 7. Application Kit

Phase 7 的 LangGraph agent：LLM 以 function calling 在 7 個工具間動態決策
（`fetch_resume`、`retrieve_job_evidence`、`compute_match`、三個 generate、
`save_artifact`），依 match score（0.5 / 0.8 門檻）條件分流，一次 run 產出並
保存三類 artifacts。artifact 採 **append-only** 版本控：生成與編輯都插新
row，「最新版」= 同 (resume, job, kind) 下 `version_number` 最大者。

共同回應 **`ApplicationKitResponse`**
```json
{
  "job_id": "uuid", "resume_id": "uuid", "match_score": 0.75,
  "tailored_resume": { "id": "uuid", "kind": "tailored_resume", "source": "agent | edit",
                       "version_number": 1, "run_id": "uuid", "resume_version_number": 1,
                       "content": { "overall_strategy": "", "section_suggestions": [],
                                    "top_keywords": [] },
                       "created_at": "datetime" },
  "cover_letter":   { "content": { "intro": "", "body_paragraphs": [], "closing": "" }, "…": "…" },
  "interview_prep": { "content": { "questions": [{ "question": "", "category": "",
                                    "why_it_matters": "", "related_resume_area": "",
                                    "answer_outline": [] }] }, "…": "…" },
  "missing": [], "errors": []
}
```
> partial 語意（NFR-4）：agent run 部分失敗仍回 `200`，缺的 kind 為 `null`
> 並列於 `missing`，降級原因在 `errors`（GET 時恆空）。

### `POST /jobs/{job_id}/generate-application-kit` → `200`
同步跑 agent（多次 AI 呼叫，30~90 秒）。

**Request**：`{ "resume_id": "uuid（可省略，預設 current resume）" }`

**錯誤**
- `404` Resume not found. / Job not found.
- `409` No parsed resume is available to build the application kit.
- `409` Job is not indexed for retrieval. Re-add the job to rebuild its index.

### `GET /jobs/{job_id}/application-kit?resume_id={uuid}` → `200`
該 (resume, job) 各 kind 的最新版。`404` = 尚未生成 / Resume not found / Job not found。

### `PATCH /artifacts/{artifact_id}` → `200`
保存使用者編輯版；後端插入新 row（`source="edit"`、版號 +1），回傳單一
artifact 物件（同 `ApplicationKitResponse` 內的 artifact 形狀）。

**Request**：`{ "content": { …對應 kind 的完整 content… } }`

**錯誤**
- `404` Artifact not found.
- `422` Artifact content does not match the expected structure for its kind.

---

## 前端對應

`frontend/src/api/` 各檔封裝上述 endpoint：

| 檔案 | 對應 |
| --- | --- |
| `client.ts` | axios 實例、Bearer 注入、401 自動 refresh 後重送 |
| `auth.ts` | `/auth/register`、`/auth/login`、`/auth/me`、`/auth/logout` |
| `resume.ts` | `/resumes/upload`、`/resumes/current`、`PATCH /resumes/{id}`、`/resumes/{id}/versions` |
| `job.ts` | `/jobs` CRUD |
| `match.ts` | `/matches/run`、`/matches` |
| `skillGap.ts` | `/jobs/{id}/skill-gap`（POST / GET） |
| `applicationKit.ts` | `/jobs/{id}/generate-application-kit`、`/jobs/{id}/application-kit`、`PATCH /artifacts/{id}` |
