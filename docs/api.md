# CareerPilot AI — API Reference

This document catalogs every public API endpoint of the backend (source: `backend/app/api/`).

- **Base URL**
  - **Development**: `http://localhost:8000` (the frontend calls the backend directly,
    configured via `VITE_API_URL` — see `frontend/src/api/client.ts`)
  - **Production (Phase 10)**: same-origin relative path **`/api`** (the production build
    does not set `VITE_API_URL`, so the client falls back to the default `/api`).
    The nginx reverse proxy (`frontend/nginx.conf`) **strips the `/api` prefix** before
    forwarding: the browser calls `/api/auth/login` → nginx forwards `/auth/login` to
    `backend:8000`. **The backend routes themselves have no `/api` prefix**; with the
    same-origin proxy there are no CORS issues either.
- **API docs**: FastAPI auto-generates Swagger UI at `/docs` and the spec at `/openapi.json`
- **Authentication**: everything except `/health` and `/auth/register|login|refresh|logout`
  requires `Authorization: Bearer <access_token>` (`app/api/deps.py::get_current_user`)
- **Data isolation**: all resources are scoped to the current user; other users' data is
  always a `404`
- **Common errors**: `401` missing / invalid access token, `422` request body validation
  failure, `429` rate limit exceeded (see below), `503` database connection failure
- **Rate limits** (Phase 9, slowapi; `app/core/ratelimit.py`): authenticated requests are
  keyed by bearer token (≈ per-user), unauthenticated requests by client IP; exceeding a
  limit returns `429 { "detail": "Too many requests. ..." }`. Defaults (all defined in
  `app/core/config.py` and overridable via environment variables, except `/auth/refresh`
  which is fixed in code):

  | Endpoint | Limit |
  | --- | --- |
  | `POST /auth/register`, `POST /auth/login` | 10/minute |
  | `POST /auth/refresh` | 30/minute (fixed) |
  | `POST /resumes/upload`, `PATCH /resumes/{id}` | 10/minute |
  | `POST /jobs` | 20/minute |
  | `POST /matches/run` | 5/minute |
  | `POST /jobs/{id}/skill-gap` | 5/minute |
  | `POST /jobs/{id}/generate-application-kit` | 3/minute |

## Endpoint Overview

| Method | Path | Auth | Description |
| --- | --- | :---: | --- |
| GET | `/health` | – | Health check (DB / pgvector) |
| POST | `/auth/register` | – | Register |
| POST | `/auth/login` | – | Log in, obtain a token pair |
| POST | `/auth/refresh` | – | Exchange a refresh token for a new token pair (rotating) |
| POST | `/auth/logout` | – | Revoke a refresh token |
| GET | `/auth/me` | ✓ | Current user |
| POST | `/resumes/upload` | ✓ | Upload a file or paste text → parse resume |
| GET | `/resumes/current` | ✓ | Current user's latest resume |
| GET | `/resumes/{resume_id}` | ✓ | Resume detail |
| PATCH | `/resumes/{resume_id}` | ✓ | Edit resume (saved as a new version) |
| GET | `/resumes/{resume_id}/versions` | ✓ | List resume versions |
| POST | `/jobs` | ✓ | Add a job (paste raw text) → parse + index |
| GET | `/jobs` | ✓ | Job list (lightweight) |
| GET | `/jobs/{job_id}` | ✓ | Job detail |
| DELETE | `/jobs/{job_id}` | ✓ | Delete a job |
| POST | `/matches/run` | ✓ | One resume × a batch of jobs, batch scoring + explanations |
| GET | `/matches?resume_id=` | ✓ | Match results for that resume (score desc) |
| POST | `/jobs/{job_id}/skill-gap` | ✓ | Run skill gap analysis (retrieve + rerank + generate) |
| GET | `/jobs/{job_id}/skill-gap?resume_id=` | ✓ | Read the existing report for that (resume, job) pair |
| GET | `/skill-gaps/{report_id}` | ✓ | Read a report by id |
| POST | `/jobs/{job_id}/generate-application-kit` | ✓ | Run the kit agent, producing all three artifact kinds in one run |
| GET | `/jobs/{job_id}/application-kit?resume_id=` | ✓ | Read the latest version of each artifact kind for that (resume, job) pair |
| PATCH | `/artifacts/{artifact_id}` | ✓ | Save an edited version (append-only, creates a new version) |

---

## 1. Health

### `GET /health`
No authentication required.

**200**
```json
{ "status": "ok", "db": "ok", "pgvector": "ok" }
```
When the DB connection fails this returns **503** with `status` `"degraded"` and `db`
`"error: <message>"` (usable as a readiness check); `pgvector` is informational only and
may be `ok` / `not_installed` / `error` / `unknown` (still 200 when not installed).

---

## 2. Auth (`/auth`)

### `POST /auth/register` → `201`
**Request**
```json
{ "email": "user@example.com", "password": "min 8 chars, max 72 bytes", "full_name": "Optional" }
```
**Response — `UserResponse`**
```json
{
  "id": "uuid", "email": "user@example.com", "full_name": "Optional",
  "is_active": true, "created_at": "2026-07-31T00:00:00Z"
}
```
**Errors**: `409` Email already registered

### `POST /auth/login` → `200`
**Request**: `{ "email": "...", "password": "..." }`

**Response — `Token`**
```json
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```
**Errors**: `401` Incorrect email or password

### `POST /auth/refresh` → `200`
**Request**: `{ "refresh_token": "..." }` → **Response**: `Token` (the old refresh token is rotated and invalidated)

**Errors**: `401` Invalid or expired refresh token

### `POST /auth/logout` → `204`
**Request**: `{ "refresh_token": "..." }` (no response body)

### `GET /auth/me` → `200`
**Response**: `UserResponse`

---

## 3. Resumes (`/resumes`)

Shared response type **`ResumeResponse`**:

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

**`ResumeParsed`** (`app/ai/parsers/resume_schema.py`; every field has a default):
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
`multipart/form-data`; **exactly one of `file` or `text_content`**.

| Field | Type | Description |
| --- | --- | --- |
| `file` | file | PDF or DOCX |
| `text_content` | string | Paste plain text directly |

**Errors**
- `400` both provided, or neither
- `413` file exceeds `MAX_UPLOAD_SIZE_MB`, or text exceeds `MAX_TEXT_INPUT_CHARS`
- `415` not PDF / DOCX
- `422` text extraction failed (e.g. empty or too short)

### `GET /resumes/current` → `200`
The latest resume. `404` No resume found for this user.

### `GET /resumes/{resume_id}` → `200`
`404` Resume not found.

### `PATCH /resumes/{resume_id}` → `200`
Submit the full structured resume; it is saved as a **new version**.

**Request**: `{ "parsed_data": { ...ResumeParsed... } }`; `404` Resume not found.

### `GET /resumes/{resume_id}/versions` → `200`
**Response — `ResumeVersionResponse[]`** (without the full `parsed_data`)
```json
[{ "id": "uuid", "version_number": 1, "label": "...", "created_at": "datetime" }]
```

---

## 4. Jobs (`/jobs`)

### `POST /jobs` → `201`
**Request**: `{ "raw_text": "raw job description text" }`

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

**`JobParsed`** (`app/ai/parsers/job_schema.py`): `company`, `title`, `location`,
`work_mode` (`remote` / `hybrid` / `onsite`), `responsibilities[]`,
`required_skills[]`, `preferred_skills[]`, `qualifications[]`, `experience_requirements[]`.

**Errors**: `422` text extraction failed

### `GET /jobs` → `200`
**Response — `JobListItem[]`** (lightweight; no `parsed_data` / `raw_text`)
```json
[{ "id": "uuid", "company": null, "title": null,
   "parse_status": "...", "index_status": "...", "created_at": "datetime" }]
```

### `GET /jobs/{job_id}` → `200`
`JobResponse`; `404` Job not found.

### `DELETE /jobs/{job_id}` → `204`
`404` Job not found.

---

## 5. Matches (`/matches`)

### `POST /matches/run` → `200`
Batch scoring with upsert (re-running overwrites existing results, hence 200 rather than 201).

**Request**
```json
{ "resume_id": "uuid", "job_ids": ["uuid", "..."] }
```
`job_ids` length 1–50 (computed synchronously; at most 2 LLM calls per job).

**Response — `MatchRunResponse`**
```json
{
  "resume_id": "uuid",
  "resume_version_number": 1,
  "results": [ /* MatchResultItem, ordered by match_score desc */ ],
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
> A `null` component inside `breakdown` means that component was **unavailable** (weights
> were re-normalized — see `weights_used`), not a score of 0.
> `updated_at` = time of the last run.

**Errors**
- `404` Resume not found.
- `409` Resume has no parsed version to match against.

### `GET /matches?resume_id={uuid}` → `200`
**Response**: `MatchResultItem[]` (`match_score` desc); `404` Resume not found.

---

## 6. Skill Gaps

Routes live under two entry points: job-scoped actions and pair lookups (`/jobs/...`),
and report-scoped reads (`/skill-gaps/...`).

Shared response **`SkillGapReportResponse`**
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
  "chunks": [{ "id": "uuid", "section": "", "content": "original chunk text" }],
  "created_at": "datetime", "updated_at": "datetime"
}
```
> `retrieval.chunks` is in vector (pre-rerank) order; `ranked_chunk_ids` is the post-rerank
> order. The top-level `chunks` array (with original text, so the frontend can expand
> citations without a second request) follows the post-rerank order.
> `dropped_gap_count` = number of gaps dropped by citation validation for having zero evidence.

### `POST /jobs/{job_id}/skill-gap` → `200`
Runs retrieval + rerank + generation with upsert (re-running overwrites the existing
report, hence 200).

**Request**: `{ "resume_id": "uuid" }`

**Errors**
- `404` Resume not found. / Job not found.
- `409` Resume has no parsed version to analyze.
- `409` Resume embeddings are unavailable for retrieval.
- `409` Job is not indexed for retrieval. Re-add the job to rebuild its index.

### `GET /jobs/{job_id}/skill-gap?resume_id={uuid}` → `200`
Read the existing report for that (resume, job) pair. `404` = not analyzed yet /
Resume not found / Job not found.

### `GET /skill-gaps/{report_id}` → `200`
Read a report by id. `404` Skill gap report not found.

---

## 7. Application Kit

The Phase 7 LangGraph agent: the LLM decides dynamically among 7 tools via function
calling (`fetch_resume`, `retrieve_job_evidence`, `compute_match`, the three generate
tools, `save_artifact`), with conditional routing on the match score (0.5 / 0.8
thresholds), producing and persisting all three artifact kinds in a single run.
Artifacts use **append-only** versioning: both generation and edits insert new rows;
"latest" = the highest `version_number` for a given (resume, job, kind).

Shared response **`ApplicationKitResponse`**
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
> Partial semantics (NFR-4): a partially failed agent run still returns `200`; missing
> kinds are `null` and listed in `missing`, with degradation reasons in `errors`
> (always empty on GET).

### `POST /jobs/{job_id}/generate-application-kit` → `200`
Runs the agent synchronously (multiple AI calls, 30–90 seconds).

**Request**: `{ "resume_id": "uuid (optional; defaults to the current resume)" }`

**Errors**
- `404` Resume not found. / Job not found.
- `409` No parsed resume is available to build the application kit.
- `409` Job is not indexed for retrieval. Re-add the job to rebuild its index.

### `GET /jobs/{job_id}/application-kit?resume_id={uuid}` → `200`
Latest version of each kind for that (resume, job) pair. `404` = not generated yet /
Resume not found / Job not found.

### `PATCH /artifacts/{artifact_id}` → `200`
Saves a user-edited version; the backend inserts a new row (`source="edit"`, version
number +1) and returns the single artifact object (same shape as an artifact inside
`ApplicationKitResponse`).

**Request**: `{ "content": { ...full content for the artifact's kind... } }`

**Errors**
- `404` Artifact not found.
- `422` Artifact content does not match the expected structure for its kind.

---

## Frontend Mapping

Each file under `frontend/src/api/` wraps the endpoints above:

| File | Covers |
| --- | --- |
| `client.ts` | axios instance, Bearer injection, auto-refresh on 401 then replay |
| `auth.ts` | `/auth/register`, `/auth/login`, `/auth/me`, `/auth/logout` |
| `resume.ts` | `/resumes/upload`, `/resumes/current`, `PATCH /resumes/{id}`, `/resumes/{id}/versions` |
| `job.ts` | `/jobs` CRUD |
| `match.ts` | `/matches/run`, `/matches` |
| `skillGap.ts` | `/jobs/{id}/skill-gap` (POST / GET) |
| `applicationKit.ts` | `/jobs/{id}/generate-application-kit`, `/jobs/{id}/application-kit`, `PATCH /artifacts/{id}` |
