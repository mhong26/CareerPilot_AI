"""eval 進入點的環境準備。

任何會 import ``app.*``（backend 程式碼）的 eval 模組，第一個 import 必須是
本模組：

1. 把 ``backend/`` 插入 ``sys.path``——eval 不安裝 backend package，直接以
   repo 相對路徑引用（開發、CI 同一套路徑）。
2. 以 setdefault 語意載入 repo 根目錄 ``.env``——``app.core.config.Settings``
   的 ``env_file=".env"`` 只在 CWD 為 repo root 時生效；這裡先手動回填，
   讓 eval 從任何工作目錄啟動行為一致（真環境變數永遠優先）。

注意：不在此設定 ``DATABASE_URL``——eval 一律使用自己的
``EVAL_DATABASE_URL`` / ``--db-url``（見 ``eval.config``），不碰 app 預設 DB。
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def _load_dotenv(path: Path) -> None:
    """極簡 .env 載入（setdefault）；只處理 ``KEY=value``，跳過註解與空行。"""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_load_dotenv(REPO_ROOT / ".env")
