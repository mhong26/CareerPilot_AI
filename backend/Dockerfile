FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

# 依賴層與程式碼層分離：pyproject 沒變就不重裝依賴、不重抓模型。
COPY pyproject.toml .

# torch 先從官方 CPU index 單獨裝（Linux 裸裝會拉 CUDA 版 ~2GB）。該 index 只
# 作用於這一步——它同時掛著 2022 年的 requests/urllib3/certifi，若以
# extra-index 讓整包解析經過它，uv 的 first-index 策略會把 HTTP stack 靜默
# 釘在帶 CVE 的舊版（phase6_notes 問題 3）。
RUN uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu

# 只裝依賴（不 build 專案本體），cache 僅綁 pyproject.toml；torch 已滿足
# 需求不會被重解析，其餘套件全部照常從 PyPI 取最新。
RUN uv pip install --system -r pyproject.toml --extra dev

# HF cache 必須在 /app 之外：docker-compose 以 ./backend:/app bind mount，
# 放 /app 內的話 build 期下載的模型會在 runtime 被 mount 蓋掉。
ENV HF_HOME=/opt/hf-cache
# Build 期預下載 cross-encoder（~90MB），runtime 離線可用（FR-26 rerank）。
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# 程式碼最後才進來：改 code 只重建以下兩層（秒級），依賴與模型層不失效。
COPY app/ app/
RUN uv pip install --system --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
