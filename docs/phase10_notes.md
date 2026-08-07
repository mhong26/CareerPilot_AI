# Phase 10 實作筆記 — Docker 打包 + CD + 文件 + 交付

> 記錄 Phase 10（打包交付）實作過程中的重要決策與踩過的坑。
> 產出物：production Dockerfiles（multi-stage、non-root、自動 migration）、
> `docker-compose.prod.yml`（nginx 反向代理 + GHCR images）、`cd.yml`
> （workflow_call CI 閘門 + multi-arch GHCR 發佈）、demo seed
> （`backend/scripts/seed.py`）、`architecture.md`、README 最終版 + 7 張截圖。
> 規劃文件見 [phase10_plan.md](phase10_plan.md)；本檔記實作時實際發生的事。

---

## 1. 重要決策（規劃時已定案的見 phase10_plan.md，此處記實作中新增的）

### 1.1 compose project name 必須顯式隔離

**坑**：`docker-compose.prod.yml` 第一次 `up` 時容器名是 `careerpilot_ai-*`——
compose 預設用「目錄名」當 project name，dev 與 prod 兩份 compose 檔共用同一個
project。後果：兩邊同時 `up` 會互相 recreate 對方的容器（dev 的 bind-mount
backend 會被 prod 的 image-only backend 頂掉，反之亦然）。

**解**：prod compose 加頂層 `name: careerpilot-prod`。容器、network、volume
（`careerpilot-prod_postgres_data_prod`）全部走獨立命名空間，dev/prod 可並存
（port 也不衝突：dev 3000/8000/5432 vs prod 只開 80）。

### 1.2 `.dockerignore` 是 production build 的隱藏前置

**坑**：repo 一直沒有 `.dockerignore`。dev build 沒炸是因為 dev compose 用
bind mount 蓋掉了 image 內容；prod build 的 `COPY . .` 會把 host（macOS）的
`frontend/node_modules`（含 mac 原生 binary）複製進 alpine 容器，**蓋掉
`npm ci` 剛裝好的 Linux 版依賴**，`npm run build` 直接壞。

**解**：兩側各加 `.dockerignore`（frontend 排除 `node_modules`/`dist`，backend
排除 `__pycache__` 等）。順帶把 build context 從 ~500MB 縮到幾 MB。

### 1.3 前端 API base URL：fallback `/api` + 兩個配套

`client.ts` 的 fallback 從 `http://localhost:8000` 改為 `/api` 之後，三種執行
模式各自的解法：

| 模式 | API 走法 |
|---|---|
| production | fallback `/api` → nginx 剝前綴代理到 backend（image 與部署位置無關） |
| Docker dev | `VITE_API_URL=http://localhost:8000` 直連（compose 已設，不受影響） |
| 裸 `npm run dev` | **會壞**——`/api` 打到 Vite dev server 沒人接。補 `vite.config.ts` 的 `server.proxy['/api']`（rewrite 剝前綴），行為與 nginx 一致 |

第三種是實作後自查抓到的 regression：README 明文支援 no-Docker 本地開發，
改 fallback 卻沒配 proxy 會讓該流程靜默壞掉。

### 1.4 editable install 跨 stage 的可行性

`Dockerfile.prod` builder stage 沿用 dev/CI 的 `uv pip install --no-deps -e .`
（而非改 non-editable，最小化與既有環境的差異）。跨 stage 可行的原因：editable
install 在 site-packages 留下指向 `/app` 的 `.pth`，runtime stage 把程式碼 COPY
到**同路徑** `/app`，路徑對得上就能 import。alembic 的 `env.py` 又自帶
`sys.path.insert`（bootstrap），兩條 import 路都通。

### 1.5 demo seed 放 `backend/scripts/`、資料自包含

規劃時已定（build context 限制）；實作補充兩點：
- `seed_data.json` 是**凍結的解析結果**（從 eval dataset 改編：Alex Rivera
  後端履歷 + 5 個梯度職缺 + Maya Chen 前端履歷），seed 只呼叫真實的
  embedding 與 `create_job_from_parsed` 路徑——約 32 段 embedding，零 LLM
  解析呼叫。
- 半途失敗會刪掉殘缺 user 再退出，plain re-run 即可重試；`--reseed` 靠 FK
  CASCADE 清資料、`llm_call_logs.user_id` SET NULL 保帳本（與 eval/seed.py
  同語意）。

---

## 2. 踩過的坑

### 2.1 nginx 預設值有兩個必炸點

- `client_max_body_size` 預設 **1MB**：履歷上傳限制是 10MB，不調直接 413。
  設 15m（上限 + 餘裕，真正的尺寸驗證仍在 backend）。
- `proxy_read_timeout` 預設 60s：application kit 生成可超過（agent deadline
  240s），設 300s 與 gunicorn `--timeout 300` 對齊。

### 2.2 screenshot 自動化：「第一個 job」不是你以為的那個

Playwright 腳本點 job 列表第一個連結截 job-detail / skill-gap / kit——但列表
是新到舊排序，第一個是最後 seed 的 **Product Marketing Manager**（最低分、
沒有 gap report 與 kit 的那個），截出來全是空狀態。改為直接指定 Senior
Backend Engineer 的 job id 重截。教訓：demo 截圖要鎖定「有資料的實體」，
不能依賴列表順序。

### 2.3 `entrypoint.sh` 的三個小但關鍵的點

- `exec gunicorn ...`：讓 gunicorn 接手 PID 1，`docker stop` 的 SIGTERM 才能
  觸發 graceful shutdown（否則 shell 吃掉訊號，等 10s 被 SIGKILL）。
- `set -e`：`alembic upgrade head` 失敗 → 容器啟動失敗 fail fast，配合
  `restart: unless-stopped` 會重試，不會帶著壞 schema 服務。
- 檔案要 `chmod +x`（git 會保留 mode bit，COPY 沿用）。

### 2.4 驗收數據（本地實測）

| 項目 | 結果 |
|---|---|
| prod image 大小 | backend 2.16GB（torch CPU + cross-encoder 為主）、frontend **76MB**（dev image 1.04GB → multi-stage 縮 93%） |
| 乾淨 volume 啟動 | migration 0001→0010 自動跑完 → gunicorn 2 workers |
| nginx 代理 | `/api/health`、register(201)、login、match、skill-gap、kit 全通 |
| SPA fallback | 深層路由刷新 200 |
| seed | 2 users + 2 resumes(各 3 embeddings) + 5 jobs(26 chunks)；重跑冪等 skip |
| demo flow | match ranking 57/36/33/31/18%、skill gap 5 條(2 high)、kit 16s 三 artifacts |
| 測試 | 前端 37 ✅、後端 170（cov 96%）✅、eval 113 ✅ |

---

## 3. Adversarial review 輪（四維度審查後的修正）

實作完成後以四個獨立審查維度（docker / cicd / frontend 整合 / docs 一致性）
掃全部變更，20 個 findings 中 13 個確認為真並修正；有代表性的：

### 3.1 CI/CD

- **`metadata-action` 的隱藏 `latest`**：預設 `flavor: latest=auto` 會在任何非
  prerelease 的 `v*` tag 也打 `latest`——對舊 commit 打 hotfix tag 會把 GHCR
  `latest` 拉回舊版（部署端預設追 latest，靜默退版）。加 `flavor: latest=false`，
  `latest` 完全由 `enable={{is_default_branch}}` 規則（僅 main）控制。
- **同一 commit 跑兩套 CI**：push main 同時觸發 `ci.yml`（`branches: ["**"]`）
  與 `cd.yml`（內部 `workflow_call` 再跑一次整套）→ 10 個 job 重複。`ci.yml`
  改 `branches-ignore: [main]`（PR 由 `pull_request` 覆蓋、main 由 CD 閘門測）。
  配套：README 的 CI badge 加 `?event=pull_request`，否則 main 無 ci.yml run
  會顯示 no status。
- **CD 無 concurrency**：連續 push main 時舊 run 可能較晚 build 完、把 `latest`
  蓋回舊 commit。加 `concurrency: group: cd-${{ github.ref }}` +
  `cancel-in-progress`（tag ref 各自成組，release 不受影響）。
- **QEMU arm64 的 torch 風險**：build 期 `import CrossEncoder` 會在 CD 的
  arm64 模擬腿載入 torch（已知 hang 熱點）。改 `huggingface_hub.
  snapshot_download`（純 HTTP，無 torch import），runtime 以
  `HF_HUB_OFFLINE=1` 驗證過模型照常從 cache 載入。

### 3.2 Production 硬化

- **空 `JWT_SECRET` 會簽出可偽造的 token**：`${JWT_SECRET}` 未設時傳空字串，
  pydantic-settings 視為已設定而覆蓋預設值 → HS256 用空 key。compose 改
  `${JWT_SECRET:?...}`（連同 `GEMINI_API_KEY`），未設直接拒起。
- **rate limit 全站共用一個桶**：gunicorn 預設只信任 127.0.0.1 的
  `X-Forwarded-For`，經 nginx 代理後所有使用者的 client IP 都是 nginx 容器 IP。
  加 `--forwarded-allow-ips "*"`（backend 不對外開 port，僅 nginx 可達，安全）。
  實測 access log 恢復真實 client IP。
- **SPA 快取策略**：index.html 無 Cache-Control 會走 heuristic caching，
  redeploy 後舊 HTML 引用已不存在的舊 hashed bundle → 白畫面。
  `/assets/`（內容定址）給 `max-age=31536000, immutable`、其餘 `no-cache`。
- **401 並發重刷登出 bug**（pre-existing，Phase 1 遺留）：refresh token 是
  單次輪替，多請求同時 401 並行重刷時輸家必失敗 → 清 token 強制登出。
  `client.ts` 加 single-flight guard（共享 promise）。
- compose 補 backend healthcheck（`/health`，DB 掛掉顯示 unhealthy 而非靜默）、
  `stop_grace_period: 90s`（kit 長請求不被 10s SIGKILL 硬殺）、轉發
  `.env.example` 列出的全部設定（JWT 參數 / 上傳限制 / rate limits——之前只轉
  一部分，使用者改了沒效果）；`.env.example` 的 `APP_ENV=development` 改為
  註解（否則會靜默把 prod 拉回 development）。

### 3.3 審查駁回的（記錄避免之後重提）

- gha cache 超量：數量級估錯，實際 backend+frontend scope 遠低於 10GB 上限。
- refresh URL 串接 trailing slash：技術上成立但僅 dev 且 `.env.example` 值
  正確，不修。
- gitleaks 在 tag push 是 no-op：屬實但每個 commit 已在 branch push 掃過，
  tag 掃描本來就是冗餘，不改。

---

## 4. 留給使用者的手動步驟（無法自動化的部分）

1. **commit / push / merge**（依 repo 慣例由使用者操作）。
2. merge main 後確認 **CD workflow 綠燈**、GHCR 出現兩個 package。
3. GHCR package visibility 改 **Public**（GitHub → Packages → 各 package →
   Package settings → Change visibility；repo 轉 public 也在此時）。
4. 補 `docs/images/langsmith_trace.png`（LangSmith 登入後截 agent trace；
   `eval/report.py` 的 Appendix B 會自動引用，重跑 run_eval 即納入報告）。
5. 發版：`git tag v1.0.0 && git push origin v1.0.0` → CD 產出
   `1.0.0` / `1.0` / `1` 標籤的 images。
6. 最終驗收（乾淨機器）：只拿 `docker-compose.prod.yml` + `.env` →
   `pull && up -d` → seed → 走完 demo flow。
