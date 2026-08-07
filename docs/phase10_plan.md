# Phase 10 實作規劃 — Docker 打包 + CD + 文件 + 交付

> 本文件是 Phase 10 的**實作前規劃**,依 [plan.md](plan.md) Phase 10 條目與 SRS NFR-14~16、Acceptance §10 撰寫。
> 每個步驟都包含:目的 → 原理(白話解釋)→ 具體做法 → 怎麼驗證。
> 已依討論確認的前提:**repo 之後會轉 public**、**seed 用凍結解析 + 真實 embedding**、**截圖由 Claude 自動嘗試**。

---

## 0. 總覽:這個 Phase 交付什麼

Phase 0~9 做出了「在開發者電腦上跑得動的系統」;Phase 10 要把它變成「**任何人拿到都能一鍵重現的正式產品**」。新增/修改的檔案一覽:

| 檔案 | 動作 | 對應 plan 條目 |
|---|---|---|
| `frontend/src/api/client.ts` | 小改:API baseURL 預設值改 `/api` | 2(反向代理前置)|
| `backend/Dockerfile.prod`、`backend/entrypoint.sh` | 新增 | 1 |
| `backend/pyproject.toml` | 小改:加 `prod` optional dependency(gunicorn)| 1 |
| `frontend/Dockerfile.prod`、`frontend/nginx.conf` | 新增 | 1、2 |
| `docker-compose.prod.yml` | 新增 | 2 |
| `.github/workflows/cd.yml` | 新增 | 3 |
| `.github/workflows/ci.yml` | 小改:加 `workflow_call` 觸發 | 3 |
| `backend/scripts/seed.py`、`backend/scripts/seed_data.json` | 新增 | 8 |
| `docs/architecture.md` | 新增 | 5 |
| `docs/api_reference.md` | 更新(沿用既有檔案,不另開 `api.md`)| 6 |
| `docs/images/*.png` | 新增(自動截圖)| 7、9 |
| `README.md` | 最終版改寫 | 4、9 |
| `.env.example` | 補充 prod 相關變數 | 4 |
| git tag `v1.0.0` | 由你手動打(見步驟 10)| 10 |

### 事先做掉的關鍵設計決策(與理由)

1. **`/api` 前綴在 nginx 剝掉,後端零改動。** 後端路由現在是 `/auth`、`/resumes`...(無前綴)。與其改後端全部路由 + 全部測試,不如讓 nginx 收到 `/api/auth/login` 時轉發成 `/auth/login`。改動面最小,dev 環境完全不受影響。
2. **Production Dockerfile 用獨立檔案(`Dockerfile.prod`),不動現有 dev Dockerfile。** dev Dockerfile 有 bind-mount、`--reload`、dev 依賴等假設,混在同一個檔案裡用 build target 切換雖然可行,但兩個檔案各自單純、比較好讀好維護。
3. **前端 production 基底用 `nginxinc/nginx-unprivileged`。** 官方 nginx 映像檔的主進程是 root;unprivileged 版是 nginx 官方維護的非 root 變體(監聽 8080),直接滿足 non-root 要求,不用自己折騰權限。
4. **CD 的「測試先過才發佈」用 reusable workflow 實現。** `ci.yml` 加上 `workflow_call:` 觸發器,`cd.yml` 直接呼叫整套 CI 當第一個 job——不用複製貼上測試步驟,CI 改了 CD 自動跟上;而且 tag push 本來不會觸發 CI(它只監聽 branch),經由 CD 呼叫就補上了。
5. **seed 放 `backend/scripts/`,不放 repo 根目錄 `scripts/`。** seed 要在 backend 容器內執行(才有 DB 連線與 app 程式碼),而 Docker build context 是 `./backend`,根目錄的檔案進不了 image。`seed_data.json` 自帶所有資料(從 eval dataset 改編),不依賴 `eval/` 目錄。
6. **API 文件沿用 `docs/api_reference.md`。** plan 寫的 `docs/api.md` 實際上已經以 `api_reference.md` 之名存在,內容完整,只需更新(補 prod base URL 說明),不另開新檔造成兩份文件。
7. **CD 產出 multi-arch image(`linux/amd64` + `linux/arm64`)。** GitHub 的 runner 是 x86(amd64),但你的 Mac 是 Apple Silicon(arm64)——只建 amd64 的話,在 Mac 上 `pull && up` 會走 QEMU 模擬,慢且偶有相容性問題。用 buildx + QEMU 同時建兩種架構,兩種機器都原生執行。代價是 CD build 時間變長(arm64 那份在模擬器裡編),若實測太慢可退回 amd64-only(文末風險節有備案)。

---

## 1. 前端小改:API 走相對路徑 `/api`

### 目的
讓 production 前端 image「與部署位置無關」——同一個 image 放到任何網域都能用。

### 原理(為什麼非改不可)

Vite 的環境變數(`VITE_API_URL`)是 **build 時就被寫死進 JS 檔**的。現在 [client.ts:11](../frontend/src/api/client.ts#L11) 是:

```ts
const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
```

如果 production build 時烘進 `http://localhost:8000`,這個 image 部署到別台機器就會壞——瀏覽器會去打「使用者自己電腦的 8000 port」。

解法:改用**相對路徑** `/api`。瀏覽器對相對路徑的解讀是「打到目前這個網站本身」,再由 nginx(步驟 3)把 `/api/...` 轉發給 backend 容器。這樣:
- image 不含任何網址,放哪都能跑;
- 前後端變成 same-origin(同源),**CORS 問題直接消失**(瀏覽器只對跨源請求做 CORS 檢查);
- backend 不需要對外開 port,攻擊面更小。

### 具體做法

只改 fallback 值,一行:

```ts
const API_URL = import.meta.env.VITE_API_URL ?? '/api'
```

- **dev 環境不受影響**:`docker-compose.yml` 本來就設 `VITE_API_URL=http://localhost:3000` 以外的明確值(`http://localhost:8000`),dev 照舊直連。
- **production build 不設 `VITE_API_URL`** → fallback 到 `/api` → 由 nginx 代理。
- [client.ts:59](../frontend/src/api/client.ts#L59) 的 refresh 呼叫用的是 `${API_URL}/auth/refresh` 字串拼接,axios 對 `/api/auth/refresh` 這種相對路徑會以目前頁面 origin 解析,行為正確,不用改。

### 驗證
- `npm test`、`npm run typecheck` 照常綠。
- dev `docker compose up` 前端功能不變。

---

## 2. Backend production Dockerfile

### 目的
產出一個精簡、安全、自我完備的後端 image:啟動時自動把資料庫升到最新 schema,然後以多進程模式服務。

### 原理

**(a) Multi-stage build(多階段建置)**
Docker image 是一層層檔案系統疊起來的,build 過程用的工具(uv、編譯暫存、pip cache)若不清除都會留在最終 image。Multi-stage 的做法:第一階段(builder)負責「安裝」,第二階段(runtime)是全新乾淨的基底,只從 builder **複製成品**(裝好的 Python 套件、預下載的 cross-encoder 模型、程式碼)。build 工具全部留在被丟棄的第一階段。

**(b) 只裝 production 依賴**
dev image 裝了 `--extra dev`(pytest、ruff、mypy...),正式環境不需要。改在 pyproject 加一個 `prod` extra 放 gunicorn,安裝時用 `--extra prod`、不帶 dev。

**(c) Non-root user**
容器預設以 root 執行,程式被攻破 = 攻擊者拿到容器內最高權限。在 runtime 階段建立普通使用者 `appuser`,`USER appuser` 後才啟動——被攻破也只有普通權限。

**(d) 啟動時 `alembic upgrade head`**
Alembic migration 是資料庫 schema 的版本控制;`upgrade head` = 把資料庫升到最新版,已是最新則什麼都不做(冪等)。放在 entrypoint 腳本裡、在啟動 API 之前執行,做到「拿到 image → 啟動 → 資料庫自動就緒」,使用者不用記得跑任何指令。

**(e) gunicorn + uvicorn workers**
dev 用單進程 uvicorn 就夠;正式環境用 **gunicorn 當進程管理員**,底下開多個 **uvicorn worker**:
- 多進程吃滿多核 CPU、同時服務更多請求;
- worker 掛掉 gunicorn 自動補一個(免費的自癒能力)。
- worker 數用 `WEB_CONCURRENCY` 環境變數控制,預設 2(小機器友善;注意 Phase 9 的 rate limit 是 in-memory per-process,多 worker 時各自計數,已知限制,README 註記即可)。

**(f) cross-encoder 模型與 HF cache**
dev Dockerfile 已把 rerank 模型(~90MB)在 build 期下載到 `/opt/hf-cache`(避開 bind mount 覆蓋問題)。prod 沿用同一招:builder 階段下載,runtime 階段整個 cache 目錄複製過去,執行期完全離線可用。

### 具體做法

`backend/Dockerfile.prod`(骨架,實作時以此為準微調):

```dockerfile
# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
# torch 單獨從官方 CPU index 裝(避免 2GB CUDA 版與舊版 HTTP stack 汙染,同 dev/CI 做法)
RUN uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu
# 只裝 production 依賴(含 prod extra 的 gunicorn;不含 dev)
RUN uv pip install --system -r pyproject.toml --extra prod
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
COPY app/ app/
RUN uv pip install --system --no-deps .

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim
RUN useradd --create-home --uid 1000 appuser
# 只搬成品:site-packages、console scripts、模型 cache
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder --chown=appuser /opt/hf-cache /opt/hf-cache
WORKDIR /app
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/
COPY scripts/ scripts/
COPY entrypoint.sh .
ENV HF_HOME=/opt/hf-cache
USER appuser
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
```

`backend/entrypoint.sh`:

```bash
#!/bin/sh
set -e
echo "Running database migrations..."
alembic upgrade head
echo "Starting gunicorn..."
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind 0.0.0.0:8000 \
  --timeout 300
```

要點解釋:
- `exec` 讓 gunicorn 取代 shell 成為 PID 1,才能正確收到 docker stop 的訊號(graceful shutdown)。
- `--timeout 300`:application kit 生成最長可到 90s+,預設 30s 會把 worker 誤殺。
- `set -e`:migration 失敗就直接讓容器啟動失敗(fail fast),而不是帶著壞 schema 開始服務。
- dev Dockerfile 沒有 `alembic/`(靠 bind mount);prod 必須把 `alembic.ini` + `alembic/` COPY 進 image,不然容器裡跑不了 migration。

`backend/pyproject.toml` 增加:

```toml
[project.optional-dependencies]
prod = ["gunicorn>=22"]
```

### 驗證
本地 `docker build -f backend/Dockerfile.prod backend/` 成功;容器啟動 log 依序出現 migration → gunicorn workers;`curl localhost:8000/health` 通;`docker exec ... whoami` 回 `appuser`。

---

## 3. Frontend production Dockerfile + nginx 設定

### 目的
把 React 程式碼編譯成靜態檔案,由 nginx 服務,並讓 nginx 兼任「`/api` 反向代理」。

### 原理

**(a) 為什麼 production 不能用 Vite dev server**
`npm run dev` 是即時轉譯 + 熱更新的開發伺服器,慢、肥、官方明言不可上線。`npm run build` 才會產出最佳化的純靜態檔(壓縮過的 HTML/CSS/JS,在 `dist/`),而「服務靜態檔」是 nginx 的看家本領。

**(b) 反向代理(reverse proxy)**
nginx 對外是唯一入口,按路徑分流:

```
瀏覽器 ──► nginx (容器內 8080)
             ├─ /api/... ──► 轉發給 backend:8000(剝掉 /api 前綴)
             └─ 其他     ──► 回傳 dist/ 的靜態檔
```

「剝前綴」靠 nginx 的一個規則:`location /api/ { proxy_pass http://backend:8000/; }` —— **`proxy_pass` 網址結尾有 `/` 時**,nginx 會把 location 匹配到的 `/api/` 換成 `/` 再轉發。`/api/auth/login` → `/auth/login`,後端完全不用知道 `/api` 的存在。

**(c) SPA fallback(`try_files`)**
React Router 的路由(如 `/jobs/123`)只存在於瀏覽器端的 JS 裡,伺服器上沒有這個檔案。使用者直接刷新 `/jobs/123` 時,nginx 找不到檔案預設回 404。解法:`try_files $uri /index.html` —— 找不到的路徑一律回 `index.html`,讓 React Router 接手。

**(d) 兩個容易踩的坑,設定裡直接處理**
- `client_max_body_size`:nginx 預設只允許 **1MB** 的請求 body,履歷上傳限制是 10MB,不調就會 413 Request Entity Too Large。
- `proxy_read_timeout`:預設 60s,application kit 生成可能超過,需要調到 300s 與 gunicorn timeout 對齊。

### 具體做法

`frontend/Dockerfile.prod`:

```dockerfile
# ---------- Stage 1: build ----------
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci                     # ci 而非 install:嚴格按 lockfile,可重現
COPY . .
RUN npm run build              # 產出 dist/;不設 VITE_API_URL → baseURL 落到 /api

# ---------- Stage 2: serve ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine   # 官方非 root 變體,監聽 8080
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 8080
```

`frontend/nginx.conf`:

```nginx
server {
    listen 8080;
    client_max_body_size 15m;              # 履歷上傳 10MB + 餘裕

    location /api/ {
        proxy_pass http://backend:8000/;   # 結尾的 / = 剝掉 /api 前綴
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;           # kit 生成長任務
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;        # SPA fallback
    }
}
```

(`backend` 這個主機名怎麼解析?docker compose 會把同一個 compose 檔裡的 service 名稱註冊成內部 DNS,容器之間直接用 service 名互連。)

### 驗證
本地 build + 用 prod compose 起來後:打開 `http://localhost` 能看到頁面、登入/上傳/刷新深層路由都正常、上傳 10MB 內檔案不被 413。

---

## 4. `docker-compose.prod.yml`

### 目的
一個檔案描述正式環境的三個服務(db / backend / frontend),支援兩種用法:
- **交付用**:`pull && up -d` 直接抓 GHCR 上的現成 image;
- **開發驗證用**:`up --build` 在本地從 Dockerfile.prod 建(CD 還沒跑之前就能驗證)。

### 原理

compose 檔裡同時寫 `image:` 和 `build:` 時:`up --build` 走本地建置並以該名稱命名;`pull` 則從 registry 抓。一份檔案兩用。

與 dev compose 的三個本質差異:
1. **不掛載原始碼**(image 是不可變成品)、不開 `--reload`;
2. **db 與 backend 不對外開 port**——只有 nginx(frontend)映射到主機的 80,backend 只在 compose 內部網路被 nginx 存取,DB 只被 backend 存取,攻擊面最小;
3. **`restart: unless-stopped`**——機器重開或容器 crash 自動拉起來,這是正式環境的基本自癒。

### 具體做法(骨架)

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-careerpilot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-careerpilot}
      POSTGRES_DB: ${POSTGRES_DB:-careerpilot}
    volumes:
      - postgres_data_prod:/var/lib/postgresql/data   # 獨立 volume,不與 dev 混用
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-careerpilot}"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  backend:
    image: ghcr.io/mhong26/careerpilot-backend:${IMAGE_TAG:-latest}
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-careerpilot}:${POSTGRES_PASSWORD:-careerpilot}@db:5432/${POSTGRES_DB:-careerpilot}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      # ...其餘與 dev compose 相同的環境變數,APP_ENV 預設 production
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-2}
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    # 注意:沒有 ports、沒有 volumes、沒有 command(用 image 內建 entrypoint)

  frontend:
    image: ghcr.io/mhong26/careerpilot-frontend:${IMAGE_TAG:-latest}
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    ports:
      - "${FRONTEND_PORT:-80}:8080"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  postgres_data_prod:
```

`IMAGE_TAG` 變數讓使用者可以鎖版本(`IMAGE_TAG=1.0.0 docker compose ... up -d`),預設追 `latest`。

### 驗證
`docker compose -f docker-compose.prod.yml up --build -d` → 三個容器健康、`http://localhost` 完整 demo flow 走得通、`docker compose down` 後再 `up` 資料還在(volume 持久化)。

---

## 5. CD:`.github/workflows/cd.yml`

### 目的
push 到 `main` 或打 `v*` tag 時,自動:跑完整 CI 測試 → build 兩個 production image → 推上 GHCR 並打好版本標籤。

### 原理

**(a) GHCR 與 `GITHUB_TOKEN`**
GHCR(GitHub Container Registry)是 GitHub 附帶的 image 倉庫(`ghcr.io/<owner>/<name>`)。GitHub Actions 每次執行都自動拿到一個臨時的 `GITHUB_TOKEN`,只要 workflow 宣告 `permissions: packages: write`,這個 token 就能推 image——**全程不需要自己建立或保存任何密鑰**。

**(b) 測試閘門:reusable workflow**
`ci.yml` 加上 `workflow_call:` 觸發器後,就變成「可被其他 workflow 呼叫的函式」。`cd.yml` 第一個 job 直接 `uses: ./.github/workflows/ci.yml`,build job 宣告 `needs: ci` —— 任何測試紅燈,image 就不會被發佈。順帶解決「tag push 不觸發 CI」的漏洞(CI 只監聽 branch push,經 CD 呼叫則 tag 也會跑測試)。

**(c) 標籤策略(`docker/metadata-action`)**
image tag 就像可以貼很多張的版本貼紙,這個 action 根據觸發情境自動算出該貼哪些:

| 觸發 | 產生的 tags | 用途 |
|---|---|---|
| 任何發佈 | `sha-<commit>` | 永遠可追溯到確切 commit,出事精確回滾 |
| push main | `latest` | demo / 快速部署永遠指向最新穩定版 |
| push tag `v1.0.0` | `1.0.0`、`1.0`、`1` | 語意化版本:使用者可鎖死 `1.0.0` 或追 `1.x` |

**(d) build cache(`type=gha`)**
Docker build 的層快取存到 GitHub Actions cache,下次 CD 只重建有變動的層,幾分鐘搞定而不是每次全量重建。

**(e) multi-arch(見決策 7)**
`setup-qemu-action` + `setup-buildx-action` 讓 x86 runner 能同時產出 amd64 + arm64 兩種架構,`docker pull` 時自動挑對的。

### 具體做法(骨架)

`ci.yml` 的 `on:` 區塊加一行:

```yaml
on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]
  workflow_call:        # 新增:允許被 cd.yml 呼叫
```

`.github/workflows/cd.yml`:

```yaml
name: CD

on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  ci:
    uses: ./.github/workflows/ci.yml   # 完整跑一遍 lint + tests + secret scan
    secrets: inherit

  build-and-push:
    needs: ci                          # 測試綠燈才發佈
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write                  # 授權 GITHUB_TOKEN 推 GHCR
    strategy:
      matrix:
        include:
          - component: backend
            context: ./backend
            dockerfile: ./backend/Dockerfile.prod
          - component: frontend
            context: ./frontend
            dockerfile: ./frontend/Dockerfile.prod
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3        # arm64 模擬
      - uses: docker/setup-buildx-action@v3      # multi-arch builder
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/${{ github.repository_owner }}/careerpilot-${{ matrix.component }}
          tags: |
            type=sha
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
      - uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.dockerfile }}
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha,scope=${{ matrix.component }}
          cache-to: type=gha,mode=max,scope=${{ matrix.component }}
```

### 你需要手動做的一次性設定(發佈後)
第一次 CD 成功後,GHCR 上的 package 預設是 **private**。到 GitHub → 你的個人頁 → Packages → `careerpilot-backend` / `careerpilot-frontend` → Package settings → Danger Zone → **Change visibility → Public**(各做一次)。之後任何人不用登入就能 `docker pull`。

### 驗證
push 到 main → Actions 裡 CD 綠燈 → GHCR 出現兩個 package;`docker pull ghcr.io/mhong26/careerpilot-backend:latest` 成功;`docker buildx imagetools inspect` 看得到 amd64 + arm64 兩個 manifest。

---

## 6. Demo seed:`backend/scripts/seed.py`

### 目的
一鍵塞入示範資料(demo 帳號 + 履歷 + 5 個職缺),讓評審 clone 下來馬上有東西可看,不用自己生資料。

### 原理(依你選的「凍結解析 + 真 embedding」策略)

系統正常的資料流是:原文 → **LLM 解析**(燒配額、慢、結果每次略不同)→ 結構化資料 → **embedding** → 入庫。seed 的技巧是跳過第一步:把「解析好的結構化結果」直接凍結在 `seed_data.json` 裡(從 `eval/datasets/` 的 25 個場景中挑選改編,那些本來就是人工校對過的高品質結構化資料),seed 時只做:

1. 建 demo 使用者(走真實的 bcrypt 雜湊,所以**可以真的登入**);
2. 直接建 `Resume` + `ResumeVersion` 記錄(凍結的 parsed 欄位);
3. 對履歷跑真實的 `generate_resume_embeddings`(真 embedding API,量小:每份履歷 3 段);
4. 對每個 job 走真實的 `create_job_from_parsed` + chunking + embedding(真實 production 路徑,只跳過 LLM 解析)。

這正是 `eval/seed.py` 驗證過的模式。embedding 是唯一的 API 花費:約 (3 段 × 2 履歷) + (5 job × ~5 chunks) ≈ 30 個文字段,遠低於配額。

### 具體做法

- `backend/scripts/seed_data.json`:2 個 demo 使用者的資料——
  - `demo@careerpilot.ai` / 密碼 `Demo1234!`:1 份後端工程師履歷 + **5 個職缺**(刻意混搭:2 個高匹配、2 個中等、1 個明顯不匹配,demo match ranking 時對比才好看);
  - `demo2@careerpilot.ai` / 密碼 `Demo1234!`:1 份前端工程師履歷、0 個職缺(用來 demo 使用者隔離:登入後看不到 demo1 的任何資料)。
- `backend/scripts/seed.py`:
  - 冪等:demo email 已存在 → 印訊息跳過;`--reseed` 旗標 → 先刪 demo users(FK cascade 帶走所有資料)再重種;
  - 只動 demo 帳號,不碰其他使用者的資料;
  - 執行方式(README 寫明):
    ```bash
    # dev
    docker compose exec backend python scripts/seed.py
    # prod
    docker compose -f docker-compose.prod.yml exec backend python scripts/seed.py
    ```
- match / skill-gap / application kit **不在 seed 裡預生成**——那些是 demo 時現場點給評審看的(展示系統真實能力),而且會燒較多配額。

### 驗證
seed 後用 `demo@careerpilot.ai` 登入 → 看得到履歷與 5 個 job;跑 match → ranking 高低分明;`demo2` 登入 → 空的。重跑 seed 不會重複塞資料。

---

## 7. 文件:architecture.md、api_reference.md、.env.example

### `docs/architecture.md`(新增)

高階架構文件,包含(用 mermaid 畫圖,GitHub 直接渲染):
1. **系統分層圖**:Frontend(React SPA)→ nginx → FastAPI → PostgreSQL + pgvector;AI Services 層(parser / embeddings / RAG / LangGraph agent)與 Gemini API、LangSmith 的關係;Eval 層獨立一塊。
2. **兩條關鍵資料流**:
   - 履歷/職缺 ingestion:上傳 → 文字抽取 → LLM structured parsing(retry / repair / fallback)→ chunking → embedding → pgvector;
   - Application kit agent:7 tools + planner 的 ReAct 迴圈 + match-score 條件分岔。
3. **部署拓撲**:dev compose vs prod compose 的差異圖(port、代理、image 來源)。

### `docs/api_reference.md`(更新)

- Base URL 說明補上 production 情境:`同源 /api 前綴,由 nginx 代理`;
- 快速核對 endpoint 清單與現行程式碼一致(Phase 9 之後若有增減)。

### `.env.example`(補充)

```
# ─── Production (docker-compose.prod.yml) ──────────────────────────
# gunicorn worker 數(預設 2)
WEB_CONCURRENCY=2
# 對外服務 port(預設 80)
FRONTEND_PORT=80
# 部署用 image tag(latest 或如 1.0.0)
IMAGE_TAG=latest
```

並在 `VITE_API_URL` 旁註記「僅 dev 使用;production 走 nginx 同源 /api,不需設定」。

---

## 8. 自動截圖(README / 文件用)

### 目的與方法
文件裡有真實畫面,demo 效果差很多。做法:

1. 起 dev stack + 跑 seed;
2. 對 demo 帳號現場跑一次 match、一次 skill gap、一次 application kit(**這步會燒少量 Gemini 配額**——約十幾個 LLM call,一次性成本);
3. 用 Playwright(`npx playwright`,臨時安裝在 scratchpad,不進 repo 依賴)寫一支腳本:登入 demo 帳號 → 依序訪問 Dashboard / Resume / Jobs / Job Detail(match + skill gap tab)/ Application Kit → 每頁存 PNG 到 `docs/images/`;
4. 挑最好的 4~6 張放進 README 與 architecture.md;
5. LangSmith trace 的截圖(agent 的 function-call 樹狀圖)無法自動化(需要你的 LangSmith 登入),**這張留 placeholder 給你手動補**,位置與檔名我會在文件裡標好(`docs/images/langsmith_trace.png`)。

### eval_report 的截圖
`docs/eval_report.md` 是 `run_eval.py` 自動生成的(檔頭明寫 do not edit by hand),不手改。做法:在 `eval/report.py` 的模板尾端加一個固定的「Appendix: LangSmith traces」小節,引用 `docs/images/langsmith_trace.png`(檔案存在才顯示得出來)——生成器改一次,之後每次重生報告都自帶。

---

## 9. README 最終版

重寫為對外展示版,結構:

1. **一句話介紹 + badges**(CI status)+ 主畫面截圖;
2. **Features**:條列 8 大功能,各配一句話(對應 SRS §2.2);
3. **AI Techniques**:RAG / LangGraph agent(7-tool function calling)/ structured outputs / embeddings / model fallback / LLM-as-judge eval——這是課程驗收「≥3 AI techniques」的明示;
4. **Screenshots** 區(步驟 8 的產出);
5. **Quick Start(dev)**:保留現有內容,加 seed 指令;
6. **Production Deployment(新)**:
   ```bash
   cp .env.example .env   # 填 GEMINI_API_KEY、JWT_SECRET
   docker compose -f docker-compose.prod.yml pull
   docker compose -f docker-compose.prod.yml up -d
   docker compose -f docker-compose.prod.yml exec backend python scripts/seed.py   # 可選
   ```
   加 GHCR image 表(兩個 image 的名稱與 tags 說明);
7. **Tests / Eval**:`pytest`(coverage 80% 門檻)、`npm test`、`python eval/run_eval.py`,連結 `docs/eval_report.md`;
8. **Docs 索引**:architecture / api_reference / eval_report / SRS / plan。

---

## 10. 收尾:驗證清單與你要做的事

### 我做(實作完成後的本地驗證)

1. `docker compose -f docker-compose.prod.yml up --build -d` → 乾淨 volume 起動 → migration 自動跑 → seed → 用瀏覽器走完 demo flow(register → resume → jobs → match → skill gap → kit);
2. `pytest -q`(backend)、`npm test`(frontend)全綠——確認 client.ts 的小改與 pyproject 變動沒有破壞任何東西;
3. dev compose 起動確認開發流程不受影響;
4. `down` 後再 `up` 確認資料持久化。

### 你做(git 與 GitHub 操作,依你的習慣由你執行)

1. 檢查工作區變更,自行 commit / push(phase10 branch → PR → merge main);
2. merge 進 main 後:確認 Actions 的 **CD workflow 綠燈**、GHCR 出現兩個 package;
3. 把兩個 GHCR package 的 visibility 改成 **Public**(步驟 5 的一次性設定;repo 轉 public 也在這時做);
4. 手動補 `docs/images/langsmith_trace.png`(LangSmith 的 agent trace 畫面);
5. 打正式版 tag:
   ```bash
   git checkout main && git pull
   git tag v1.0.0
   git push origin v1.0.0
   ```
   → CD 再跑一次,GHCR 出現 `1.0.0` / `1.0` / `1` 標籤;
6. (最終驗收)找一台乾淨機器(或 `docker system prune` 後的本機):只拿 `docker-compose.prod.yml` + `.env` 兩個檔案,`pull && up -d && seed` → 完整 demo flow 走通。

---

## 風險與備案

| 風險 | 備案 |
|---|---|
| multi-arch build(QEMU 模擬 arm64)讓 CD 太慢(>30min)或 torch arm64 wheel 缺失 | 退回 `platforms: linux/amd64`;Apple Silicon 上以 Rosetta/QEMU 模擬執行(Docker Desktop 支援,只是慢) |
| backend image 偏大(torch CPU + 模型,估 1.5~2GB) | 可接受(GHCR 無硬限制);已用 slim base + multi-stage,torch 是 rerank 功能的必要成本 |
| gunicorn 多 worker 下 in-memory rate limit 各自計數 | 已知限制,README 註記;正式修法(Redis backend)超出本課程範圍 |
| 自動截圖畫面狀態不理想(loading 中、資料未渲染完) | Playwright 腳本加明確的等待條件;真不行則列出需要你手動補的清單 |
| seed 的 embedding 呼叫遇到 free-tier 節流(429) | 量小(~30 段)通常無感;必要時比照 eval 加 per-text 間隔 |
| `workflow_call` 下 gitleaks job 的 token 權限 | `secrets: inherit` 已涵蓋;若有意外改為 CD 內獨立宣告 permissions |

---

## 建議施工順序(依依賴關係)

```
1. client.ts 小改 + pyproject prod extra          (前置,5 分鐘)
2. backend/Dockerfile.prod + entrypoint.sh        ┐
3. frontend/Dockerfile.prod + nginx.conf          ├ 本地可獨立驗證
4. docker-compose.prod.yml → 本地 up --build 驗證  ┘
5. seed.py + seed_data.json → 本地 seed 驗證
6. cd.yml + ci.yml workflow_call                  (推上去才驗得到,先寫好)
7. architecture.md / api_reference.md / .env.example
8. 自動截圖(需要 4、5 完成)
9. README 最終版(需要 8 的圖)
10. 你執行:commit / push / merge / package public / tag v1.0.0
```
