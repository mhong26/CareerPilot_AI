from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> JSONResponse:
    """Health probe：DB 連不上回 503（degraded），可供 readiness check 使用。

    pgvector 僅為資訊性欄位：extension 未安裝仍回 200（app 本身可服務），
    只有 DB 連線層失敗才算 degraded。
    """
    db_status = "ok"
    pgvector_status = "unknown"

    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    try:
        row = db.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        pgvector_status = "ok" if row else "not_installed"
    except Exception:
        pgvector_status = "error"

    healthy = db_status == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "db": db_status,
            "pgvector": pgvector_status,
        },
    )
