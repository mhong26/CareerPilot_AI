# Phase 3 實作筆記 — 履歷 ingestion 與解析

Phase 3（FR-7~12）實作過程遇到的所有問題、原因與解法整理。分三類：
**AI / Gemini 結構化解析**（最核心）、**環境 / 設定**、**開發小坑**。

---

## 一、AI / Gemini 結構化解析（最核心、最花時間）

### 問題 1：Gemini 拒絕 schema —「Unknown field for Schema: default」
- **原因**：用 Pydantic 定義履歷格式時給欄位設了預設值。Google SDK 把它轉成 Gemini
  的 response_schema 時會夾帶 `default` 鍵，而 Gemini 的 schema 不認得 `default`，整包被拒。
- **解法**：新增 `backend/app/ai/llm/schema_utils.py` 的 `to_gemini_schema()`，送給 Gemini
  前把 `default`、`$ref`、`$defs`、`title` 等不支援的鍵清掉、$ref 內聯；**驗證時仍用原本
  帶預設值的 Pydantic 模型**。一份 schema、兩種用途分離。

### 問題 2：稀疏履歷會解析失敗（時好時壞）
- **原因**：一度為了繞開問題 1，把所有欄位改成必填。結果履歷缺某資訊（例如沒寫 email）時，
  Gemini 會直接省略該欄位，必填驗證就失敗 → 解析整個壞掉。欄位齊全的履歷才會成功。
- **解法**：配合 `to_gemini_schema`，欄位改回帶預設值（容錯）。Gemini 省略時驗證自動補空，
  不再失敗。同時修掉「舊資料缺欄位讀取會壞」的隱憂。

### 問題 3：Skills 被抓成「整行」
- **原因**：履歷把技能按分類分行（「Programming Languages: Python, Java...」），模型把整行
  當成一個技能。
- **解法**：在 `skills` 欄位加 `Field(description=...)` 指示「拆成個別技能、丟掉分類標籤」。

### 問題 4：Projects 只抓到第一個 bullet
- **原因**：`ProjectItem` 當初只有 `description`（單一字串）、沒有 bullets 欄位，多條 bullet
  只有第一條被塞進 description，其餘丟失。
- **解法**：`ProjectItem` 加 `bullets: list[str]`（後端 schema + 前端型別 + 編輯器都補）。

### 問題 5：兩個字串陣列互搶（bullets 全跑進 tech）
- **原因**：project 同時有 `bullets` 和 `tech` 兩個字串陣列，模型分不清用途，把全部倒進 `tech`。
- **解法**：給兩個欄位各加明確 `Field(description=...)`：bullets=成果描述、tech=只放技術名。

### 問題 6：Experience 日期+公司+職稱全擠進 start_date
- **原因**：履歷把「Jan 2026 - May 2026  Appy.yo  Software Engineer」寫在同一行，模型不知道
  邊界，整串塞進 start_date。
- **解法**：給 company / title / start_date / end_date 各加欄位說明，明確界定（start 只放
  開始日期，不要含範圍/公司/職稱）。

### 問題 7（最隱蔽）：`title` 欄位永遠是空的
- **原因**：這是自己寫的 bug。`to_gemini_schema` 為了清掉 JSON Schema 的 `title` **關鍵字**，
  把所有叫 `"title"` 的鍵都刪了——結果連名字剛好叫 `title` 的**欄位**也被誤刪，導致 title
  從頭到尾沒被送給 Gemini，任何模型都填不了。
- **解法**：修正轉換邏輯——`properties` 底下的 key 是「欄位名稱」，不能當關鍵字清除；
  只在 schema 節點層級清 metadata。

### 問題 8：flash-lite 模型能力不足
- **原因**：即使前面都修好，`gemini-2.5-flash-lite` 仍會把 bullets 倒進 title、漏抓 bullets。
  同一份履歷實測，`gemini-2.5-flash` 完全正確、flash-lite 錯。
- **解法**：升級到 `gemini-2.5-flash`。

---

## 二、環境 / 設定

### 問題 9：改了 .env 的模型卻沒生效
- **原因**：兩層問題疊加——(a) docker-compose 的 `environment:` 沒列 `GEMINI_MODEL`、容器內
  也沒 .env 檔，所以實際吃的是 `config.py` 的預設值；(b) `docker compose restart` 不會重讀
  環境變數（建立容器時才注入）。
- **解法**：`config.py` 預設改成 flash、`docker-compose.yml` 補上 `GEMINI_MODEL`/`EMBEDDING_MODEL`
  讓 .env 能覆蓋，再用 `docker compose up -d`（而非 restart）重建容器。

### 問題 10：Gemini 免費額度 429（每天 20 次）
- **原因**：免費方案每天只有 20 次 `generate_content`，測試時用爆。
- **解法**：等隔天額度重置。（網路層暫時性 429 本來就有指數退避重試，但「每日上限」重試
  也沒用，只能等。）

---

## 三、開發過程的小坑

### 問題 11：mypy 型別不符
- **原因**：DB 的 `parsed_data` 是 `dict`（JSONB），但 API 回應型別要 `ResumeParsed`。
- **解法**：組裝回應時用 `ResumeParsed.model_validate(...)` 明確轉換。

### 問題 12：Migration 編號
- **原因**：autogenerate 產出的 migration 是隨機亂碼 id，跟既有 `0001/0002/0003` 序號不一致。
- **解法**：改名成 `0004_resume_tables.py`、`revision="0004"`。版本設計也刻意「不互相指」，
  避免循環外鍵，讓 migration 單純。

### 問題 13：測試清理漏表
- **原因**：測試每次跑完要清空資料庫，但 `conftest.py` 的 `TRUNCATE` 只清了舊的 auth 表。
- **解法**：補上 `resumes, resume_versions`。

### 問題 14：前端 multipart 上傳 + 小型別問題
- **原因**：axios 預設 Content-Type 是 JSON，上傳檔案要讓瀏覽器自己帶 multipart 邊界；
  另外 `ReactNode` 沒 import。
- **解法**：上傳請求把 Content-Type 設 `undefined`、補 `import type { ReactNode }`。

---

## 一句話教訓

> 跟 LLM 要結構化資料，「schema 怎麼定義」和「欄位怎麼描述」的影響往往比 prompt 還大；
> 而且要分清楚是 **schema 設計問題、自己的轉換 bug、還是模型能力不足**——這次三種都遇到了，
> 分層排查才找得到真因（尤其 `title` 那個是自己誤刪欄位，不是模型的錯）。
