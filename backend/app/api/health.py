from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict:
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

    return {
        "status": "ok",
        "db": db_status,
        "pgvector": pgvector_status,
    }
