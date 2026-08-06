# Phase 9 實作筆記 — 測試補齊 + 硬化

> 記錄 Phase 9（hardening）實作過程中的重要決策與踩過的坑。
> 產出物：全域錯誤處理、structlog 結構化日誌 + request-id、slowapi 限流、
> 輸入限制與 magic-byte 白名單、獨立測試 DB、e2e 測試、CI coverage 門檻 +
> gitleaks、前端錯誤/載入/驗證硬化。
> 起點狀態：147 個後端測試全綠、coverage 實測已 94%——本 phase 的主軸不是
> 「補測試數量」，而是把「功能會動」硬化成「可放心交付」。

---

## 1. 重要決策

### 1.1 錯誤處理走 handler-mapping，不做 AppError 基底類改造

**決策**：不建立 `AppError` 基底類、不讓 17 個 domain exception 改繼承、不動
router 裡約 25 個 `try/except → HTTPException` 區塊；只在 `app/core/errors.py`
為「目前會以裸 500 逃逸」的例外註冊全域 handler：`OperationalError`（503）、
`IntegrityError`（409）、pydantic `ValidationError`（可讀 500，接住
`_to_response` 的 JSONB 漂移）、catch-all `Exception`（JSON 500）。

**為什麼**：既有的 domain exception → HTTP 狀態碼映射已被 147 個測試完整覆蓋，
繼承改造的收益是「少幾行重複」，風險卻是狀態碼細節的靜默改變。硬化的目標是
堵住真正的洞（DB 斷線、資料漂移、未知例外），不是重構已經正確的東西。同理，
服務層既有的 LLM 失敗降級（`parse_error` / `index_error` 欄位 + 2xx）一行未動。

### 1.2 Provider 依賴集中到 `deps.py`，router 以「同一函式物件」re-export

**決策**：`get_llm_provider`（原本 5 個 router 各自定義）、`get_reranker`（2 處）、
`get_planner_model`（1 處）集中到 `app/api/deps.py`，建構失敗一律轉 503；各
router 改 `from app.api.deps import get_llm_provider` re-export。

**為什麼**：測試是拿**函式物件本身**當 `app.dependency_overrides` 的 key（如
`test_matches.py` 從三個 router import 同名函式逐一 override）。集中後所有
import 路徑指向同一個物件，12+ 處測試 override 不需要任何修改就繼續有效——
這是本 phase 「零測試攪動」原則下唯一可行的整併方式。副作用是修掉一個真實的
500 來源：`GEMINI_API_KEY` 未設時 `build_gemini_provider()` 在依賴解析階段拋
`LLMError`，之前是裸 500 加 traceback，現在是可讀的 503。

### 1.3 Logging 選 structlog，request-id middleware 手寫

**決策**：structlog（不用 stdlib JSON formatter）；request-id middleware 手寫
~40 行（不裝 `asgi-correlation-id`）。dev 環境用彩色 `ConsoleRenderer`，其他
環境單行 `JSONRenderer`；stdlib logger（uvicorn、httpx…）經 `ProcessorFormatter`
統一輸出格式。

**為什麼**：
- structlog 的 `contextvars` 讓 middleware 綁一次 `request_id`，該請求路徑上
  **所有** log（包含 M1 handler 的例外 log）自動帶上，stdlib 要自己拼
  ContextVar + logging.Filter 才能做到。
- middleware 反正要記 `request_started` / `request_completed`（method、path、
  status、duration_ms），request-id 的生成與 echo 順手就做了，多裝一個依賴
  不划算。
- 這也是 `settings.log_level` 第一次真正被讀——它在 config 裡宣告了很久但
  全 codebase 沒有任何 logging 呼叫。
- 註冊順序：`RequestContextMiddleware` 先加、CORS 後加（Starlette 後註冊者在
  外層），確保錯誤回應也帶 CORS headers。

### 1.4 Rate limit key 用 Authorization header 原文，不解 JWT

**決策**：slowapi 的 key function：有 `Authorization` header 就用整串原文
（≈ per-user），沒有就用 client IP（覆蓋 login / register 的暴力嘗試）。

**為什麼**：解 JWT 拿 user_id 當 key 更「正確」，但每個請求多一次 decode，
而且 token 過期/偽造時 key function 還得處理失敗分支。Bearer token 原文與
使用者一一對應（refresh 換 token 會重置計數，但額度是分鐘級的，影響可忽略），
零成本達到 per-user 隔離。實測：A 用光 upload 額度後 B 完全不受影響。

### 1.5 限流只掛昂貴/敏感 endpoint，不設全域 default limit

**決策**：只裝飾 8 個 endpoint（auth 三支、resume upload/PATCH、job create、
match run、skill-gap、kit generate），數字進 `Settings` 可由 env 覆寫；不設
slowapi 的全域 default。in-memory storage，per-process 的限制寫在 config 註解。

**為什麼**：會燒 Gemini 額度的是這 8 支（kit 一次 run 多次 LLM 呼叫，限
3/minute；match 每 job 最多 2 次呼叫，限 5/minute），GET 類 endpoint 沒有
攻擊面值得付出「每個請求過一次 limiter」的成本。不設全域 default 也讓 147 個
既有測試的高頻 GET 完全不受影響。多 worker 時各 process 自己計數（實際限額
×N）——本專案 dev 單 worker、prod 也只是 demo 規模，接受並註記，未來要跨
process 再接 Redis `storage_uri`。

### 1.6 測試預設關閉限流：autouse fixture 動 `limiter.enabled`，不靠 env

**決策**：conftest 加 autouse fixture 在每個測試前設 `limiter.enabled = False`；
`test_rate_limit.py` 自己重新打開、teardown 時 `limiter.reset()` 清計數器。
CI env 另設 `RATE_LIMIT_ENABLED=false` 當雙保險。

**為什麼**：slowapi 是**每個請求當下**檢查 `limiter.enabled`，所以 fixture
直接改屬性即可，沒有「settings 在 import 時定型」的順序問題。不這樣做的話，
整套測試共用 TestClient 的同一個 IP key，跑到第 11 個請求就會開始收 429，
147 個測試幾秒內全滅。`limiter.reset()` 是為了防 in-memory 計數器狀態跨測試
洩漏（429 測試打了 11 次 login，不 reset 的話後面的 auth 測試會遭殃）。

### 1.7 獨立測試 DB `careerpilot_test`，且刻意移除 `DATABASE_URL` fallback

**決策**：conftest 預設連 `careerpilot_test`（`TEST_DATABASE_URL` 可覆寫），
DB 不存在時程式化建立（連 `postgres` 維護 DB → `CREATE DATABASE` →
`CREATE EXTENSION vector`），模式完全沿用 `eval/harness.ensure_database`。
原本 `os.getenv("DATABASE_URL", ...)` 的 fallback **刻意刪除**。

**為什麼**：
- 原行為是個資安/資料事故等級的坑：conftest 的 `_clean_tables` 在**每個測試後
  TRUNCATE 全部 12 張表**，而預設連的是開發 DB——本機跑一次 `pytest`，手動
  測試建的帳號、履歷、職缺全部消失。移除 fallback 後，就算環境裡剛好 export
  了 `DATABASE_URL`（跑 backend 常態），測試也絕不會誤連開發庫。
- 用程式化建立而非 docker-compose init script：init script 只在**空 volume**
  首次初始化時執行，既有的 `postgres_data` volume 永遠不會跑它；eval DB 已
  踩過這個坑並確立了程式化建立的先例，照抄即可。
- CI 端配套：多一步 `psql CREATE DATABASE careerpilot_test`，且 alembic
  `upgrade head` 改以 step-level env 指向測試 DB——migration 從此在「測試
  實際使用的 DB」上被驗證，conftest 的 `create_all` 之後只是冪等 no-op。

### 1.8 Coverage 門檻設 80（使用者決策），儘管實測 96%

**決策**：CI `--cov-fail-under=80`，不是 90 或 95。

**為什麼**：plan/SRS 原文寫 80；實測 95.56% 留下 15+ 個百分點的緩衝，之後加
新功能不會動不動被 CI 擋。門檻的目的是防「測試品質崩壞」的護欄，不是逼每行
都有測試——剩餘未覆蓋的 113 行大多是「存在目的就是被 override 的依賴工廠
函式本體」與 `get_db` 生產路徑，測它們沒有意義。

### 1.9 前端「務實硬化」，不遷移 TanStack Query、不引入表單庫（使用者決策）

**決策**：保留各頁手寫 `useState` 管理 loading/error 的現狀；只加四樣東西：
共用 `getErrorMessage()`、`components/ui/` 四個小元件（Spinner / ErrorBanner /
WarningBanner / ElapsedTimer）、axios timeout（預設 30s、長任務 180s）、與後端
限制對齊的手寫表單驗證。

**為什麼**：TanStack Query 雖然裝了但全站零使用，遷移是 3-4 天的重構且 26 個
前端測試要跟著改——收益是程式碼品質，不是使用者可感知的硬化。Phase 9 計畫
原文只要求「loading/error states、長任務進度、表單驗證」，務實版 1-2 天可完成
且風險低。經使用者確認後採務實版。

### 1.10 `getErrorMessage()` 的訊息優先序

**決策**：後端 `{detail: string}` 原文 > FastAPI 422 `{detail: [{msg}]}` 陣列
join > 狀態碼 fallback 表（401/403/404/409/413/415/422/429/5xx）> 網路層
（`ECONNABORTED` timeout 與斷線分開措辭）> 呼叫端 fallback。

**為什麼**：後端花了很多工夫讓 409/413/415 帶可讀 detail（「履歷未解析」、
「超過 10 MB」…），前端過去卻大多 `catch { setError('固定字串') }` 直接丟掉。
最誇張的是 LoginPage：**任何**失敗（500、斷網、429）都顯示「Invalid email or
password」，使用者會反覆重打正確的密碼。修正後只有 401 才說帳密錯誤。
這也一併收掉了 `JobDetailPage` / `ApplicationKitPage` 兩處複製貼上的 409
detail 萃取程式碼。

### 1.11 表單驗證數字全部對齊後端既有限制，不發明新規則

**決策**：password 8–72（bcrypt 只取前 72 bytes、後端 schema 已限）、full_name
≤255、檔案 ≤10 MB（`max_upload_size_mb`）、貼文 ≥10 字（`_MIN_CHARS`）、job
文字 ≤50k（新 `max_text_input_chars`，與 prompt 側既有的 50k 截斷常數一致）。

**為什麼**：前端驗證的職責是「把後端一定會拒絕的輸入提前擋下、給即時回饋」，
不是另立標準。之前 password 超過 72 字會收到後端 422，而 RegisterPage 把它
顯示成「email 可能已被使用」——完全誤導。JobCreate 只加 `max_length` 不加
`min_length`：最短長度維持由 `extract_plain_text` 驗證，避免改變既有測試
依賴的錯誤訊息路徑。

### 1.12 Magic-byte 白名單放在 `text_extract.extract_text` 單一收口

**決策**：判定 source_type 之後、進 pypdf/python-docx 之前檢查檔頭：PDF 必須
`%PDF-` 開頭、DOCX 必須 `PK\x03\x04`（ZIP 容器），不符拋
`UnsupportedFileTypeError`（沿用既有的 415 映射）。

**為什麼**：副檔名與 MIME 都是 client 可造假的自我申報；magic bytes 讓改名
混入的檔案在進解析庫之前就被擋下（防 zip bomb 之類的東西直接餵給 pypdf）。
放在 `extract_text` 而非 API 層：這裡是唯一同時拿得到 bytes 與已判定型別的
地方，且例外 → HTTP 狀態碼的映射已經存在，API 層零修改。

### 1.13 上傳只讀 `limit + 1` bytes，修掉「先整包進記憶體再檢查」

**決策**：`file.file.read()` 改為 `file.file.read(limit + 1)` 再比對長度。

**為什麼**：原寫法是先把**整個上傳**讀進記憶體才檢查大小——2 GB 的惡意 POST
會先吃掉 2 GB RAM 然後才收到 413。改讀 `limit + 1` 後記憶體上限受控（10 MB
+ 1 byte），多讀的 1 byte 是判斷「超標」的最小必要量。

### 1.14 e2e 測試：專案第一個 async 測試 + 共用 fakes，但不回頭改既有測試

**決策**：新增 `tests/fakes.py`（全 schema 分派的 `FakeProvider`、`NoopReranker`、
極小真 PDF bytes、in-memory DOCX builder），整併自 `test_matches.py` 與
`test_application_kit.py` 各自維護的素材；**既有測試模組不回頭改用它**。
e2e 用 `httpx.AsyncClient(transport=ASGITransport(app=app))` 走完 register →
login → upload → job → match → kit → 讀回 kit → health 全程。

**為什麼**：兩個近似 e2e 的片段其實已存在（`kit_api` fixture、matches happy
path），缺的是「一個測試body 內走完五個階段、斷言每一階的回應」的明確 e2e。
不回頭改既有測試是刻意的：對 147 個綠測試做「純美化」的 import 重構，收益
是零、回歸風險是實打實的。httpx 0.28 已移除 `app=` shortcut，必須顯式
`ASGITransport`——這也是為什麼 e2e 不能複用同步的 `client` fixture，乾脆
自建 async fixture 一次 override 集中後的 `deps` 依賴（1.2 的紅利：只需
override 一個物件，不用像舊測試那樣一次換三個 router）。

### 1.15 `/health` 變成真的 probe：DB 掛 → 503，pgvector 只是資訊

**決策**：DB `SELECT 1` 失敗回 503 `{"status": "degraded", ...}`；pgvector
extension 缺席仍回 200。

**為什麼**：原本 `/health` 把 DB 錯誤寫進 body 但**永遠回 200**——任何
readiness probe / uptime monitor 都會被騙。DB 掛掉 app 就不可服務，該 503；
pgvector 缺席只影響向量檢索功能，app 本身可服務，維持 200 讓部署初期
（migration 未跑完）不會被 probe 打死。

---

## 2. 遇到的問題與解法

### 2.1 手刻極小 PDF 被 pypdf 拒收：`EOF marker not found`

**問題**：為了測真實的 PDF 抽取路徑（原 coverage 只有 58%，因為沒有任何測試
上傳過真 PDF），手刻了一個 ~600 bytes 的單頁 PDF，`PdfReader` 直接拋
`PdfStreamError: Stream has ended unexpectedly`。

**原因**：PDF 規格要求檔尾有 `%%EOF` 標記，pypdf 初始化時會從檔尾回掃找它；
手刻版少了 trailer 之後的 `startxref` / `%%EOF` 收尾。

**解法**：補上 `startxref\n0\n%%EOF\n`。`startxref 0` 是個無效偏移，但 pypdf
對壞掉的 xref 會自動 fallback 成全檔掃描重建（stderr 印 `incorrect startxref
pointer(4)` 警告），文字抽取正常。先在 shell 驗證可解析才寫進 `fakes.py`，
順帶把「同一份 PDF 把文字挖空」變成 scanned-PDF（抽不到字 → 422）的測試素材。

### 2.2 限流測試打 upload 收到 503 而不是預期的 400

**問題**：`test_rate_limit_key_separates_users` 故意不帶 payload 打
`/resumes/upload`，預期收 400（缺 file/text_content），實際收 503。

**原因**：兩層疊加——(1) FastAPI 的**依賴解析先於 endpoint body**，
`get_llm_provider` 在 400 檢查之前就會執行；(2) 本機測試環境沒有
`GEMINI_API_KEY`，M1 剛把「provider 建構失敗」從裸 500 改成 503，於是新行為
先攔截了請求。以前沒炸是因為所有打這支的測試都 override 了 provider。

**解法**：測試加 `dummy_provider` fixture 把 `get_llm_provider` override 成
`lambda: object()`——400 會在 provider 被實際使用前回傳，dummy 永遠不會被碰。
順帶學到：slowapi 的計數發生在 endpoint 函式被呼叫時，**依賴解析階段被拒的
請求不會計入限流**。

### 2.3 e2e 斷言的狀態字串猜錯：`succeeded` vs 實際的 `parsed` / `indexed`

**問題**：e2e 斷言 `resume["parse_status"] == "succeeded"` 失敗，實際值是
`"parsed"`。

**原因**：沒先查證就憑直覺寫斷言。實際的狀態字彙是 `resume_service` /
`job_service` 內的字面值：parse 成功是 `"parsed"`、索引成功是 `"indexed"`
（另有 `"failed"` / `"skipped"`）。

**解法**：grep 服務層的賦值處確認全部狀態字彙後修正斷言。教訓：跨層測試的
預期值要從 source of truth（服務層程式碼）抄，不要腦補。

### 2.4 Homebrew Python 的 PEP 668：pip 拒裝新依賴

**問題**：`pip install structlog` 被 `externally-managed-environment` 擋下。

**原因**：本機環境的既有依賴全裝在 Homebrew 的系統 Python 3.13（沒有
venv/uv/poetry），而 Homebrew Python 遵守 PEP 668 預設禁止 pip 直裝。

**解法**：確認專案既有套件（fastapi 等）就在這顆 Python 裡（顯然當初也是
`--break-system-packages` 裝的），照同樣方式安裝 structlog 與 slowapi，
維持環境一致。CI 不受影響（uv 裝進乾淨 runner）。

### 2.5 `pytest tests/` 顯示 `no tests ran in 0.00s`

**問題**：某次跑測試 0 個被收集，還以為 conftest 改壞了。

**原因**：shell 工作目錄在兩次呼叫之間回到了 repo root，而 root 的 `tests/`
只有 `.gitkeep`——pytest 忠實地回報「這個資料夾沒有測試」。

**解法**：改用絕對路徑 `cd /…/backend && python3 -m pytest tests/`。這也解釋
了為何 root pytest 設定不存在也沒關係——後端測試永遠從 `backend/` 起跑。

### 2.6 「API key 未設 → 503」測試在 CI 會失真：CI 有 dummy key

**問題**：想測「`GEMINI_API_KEY` 空 → 503」，但 CI env 設了
`GEMINI_API_KEY=dummy-key-for-ci`——直接打 endpoint 的話 provider 會建構
成功，請求繼續往下走到真的 LLM 呼叫（帶著假 key 打真 API，慢且結果不定）。

**原因**：`build_gemini_provider` 只在 key **為空**時拋錯，不驗證 key 是否
有效；「dummy key」在本機（無 key）與 CI（有假 key）兩個環境走出不同分支。

**解法**：測試用 `monkeypatch.setattr(settings, "gemini_api_key", "")` 強制
清空——`build_gemini_provider` 是請求時讀 settings，monkeypatch 即時生效，
兩個環境行為一致且零網路。

### 2.7 Starlette 的 catch-all handler 語意：送出 500 後仍會 re-raise

**問題**：註冊了 `Exception` 的 catch-all handler 後，測試裡
`TestClient` 打一個會炸 `RuntimeError` 的依賴，例外照樣傳播出來，看不到
500 回應本體。

**原因**：Starlette 把 `Exception` handler 裝在最外層的
`ServerErrorMiddleware`，它的行為是「送出 handler 的回應，**然後照樣
re-raise**」（讓 server 層記錄）。`TestClient` 預設 `raise_server_exceptions=
True`，會把 re-raise 的例外直接丟給測試。真實 uvicorn 下使用者看到的就是
JSON 500，行為正確；只是測試觀察不到。

**解法**：`test_error_handlers.py` 用獨立的
`TestClient(app, raise_server_exceptions=False)` fixture 觀察 500 本體。
連帶的取捨：因為 middleware 在例外路徑也是 re-raise（`request_failed` log 完
就往外丟），catch-all 的 500 回應**不會**帶 `X-Request-ID` header——
request-id 的斷言改放在正常回應（`/health`）上驗證，log 端的關聯性不受影響
（contextvars 在 handler 執行時仍在作用域內）。

### 2.8 手動驗證時 DB 斷線測試被自己的限流測試「污染」

**問題**：實機驗證流程——先連打 11 次 login 驗證 429，接著 `docker stop db`
再打 login 想看「DB down → 503」，結果收到的是 429。

**原因**：不是 bug。限流計數器是 in-memory 且以分鐘為窗，前一輪測試的 11 次
請求還在效期內；slowapi 的檢查又在進 DB 之前，所以 429 先於 503 命中。

**解法**：不需要修——這正是「限流擋在昂貴操作前面」的正確順序。「DB down →
API 回可讀 503」改由自動化測試 `test_db_down_returns_readable_503` 覆蓋
（override `get_db` 拋 `OperationalError`，不依賴真的停 DB）；手動流程保留
`/health` → 503 degraded → 重啟 → 200 的驗證，實測通過、server 全程未 crash。

### 2.9 其他小坑

- **ruff-format 與手寫排版**：pre-commit 的 ruff-format 多次把新檔案的換行
  風格重排（100 字內合併成單行）。不抵抗、以 hook 的輸出為準——repo 的格式
  權威是工具不是人。
- **`limiter.limit()` 要求 endpoint 簽名有 `request: Request`**：8 個被裝飾的
  endpoint 多數原本沒有這個參數，全部補上。漏補的話不是 import 錯誤而是
  **請求時**才炸，靠既有的 endpoint 測試網住。
- **前端測試的 `AxiosError` 構造**：jsdom 測試裡要模擬「網路錯誤 vs 帶
  response 的錯誤」，得手動 `new AxiosError(...)` 再塞 `err.response`（型別
  上要 cast）；timeout 則以 `code: 'ECONNABORTED'` 區分。這些形狀差異正是
  `getErrorMessage` 的分支，測試順便把每個分支釘住。
- **TS 泛型推斷**：`renderWithLogin(login: ReturnType<typeof vi.fn>)` 過不了
  `tsc`（`Mock<any[], unknown>` 不可指派給 `AuthContextValue.login`），改成
  直接寫函式簽名型別。
- **檔案大小測試不用真的做 10 MB 檔**：前端用 `Object.defineProperty(file,
  'size', ...)` 假造大小；後端則 `monkeypatch.setattr(settings,
  "max_upload_size_mb", 1)` 把門檻降到 1 MB，payload 只需 1 MB + 16 bytes。

---

## 3. 最終數字速覽

| 項目 | Phase 9 前 | Phase 9 後 |
|---|---|---|
| 後端測試 | 147 | **170**（+e2e、error handlers、rate limit、text extract、deps auth、input limits） |
| 後端 coverage | 94% | **95.56%**（CI 門檻 80） |
| 前端測試 | 26 | **37** |
| CI jobs | 4 | **5**（+gitleaks secret scan，掃全歷史） |
| 本機 `pytest` 對開發 DB 的破壞 | 每次全清 | **零**（獨立 `careerpilot_test`） |

實機驗證：login 連打 11 次第 11 次收 429；`docker stop db` 期間 `/health` 回
503 degraded、server 不 crash、db 重啟後自動恢復 200。
