from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.application_kits import router as application_kits_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.matches import router as matches_router
from app.api.resumes import router as resumes_router
from app.api.skill_gaps import router as skill_gaps_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import limiter

configure_logging()

app = FastAPI(
    title="CareerPilot AI",
    version="0.1.0",
    description="AI-powered resume and job match platform",
)

app.state.limiter = limiter
register_exception_handlers(app)

# 註冊順序：後註冊者在外層——CORS 放最後註冊（最外層），
# 讓錯誤回應也帶 CORS headers；RequestContextMiddleware 在內側計時與記錄。
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(resumes_router)
app.include_router(jobs_router)
app.include_router(matches_router)
app.include_router(skill_gaps_router)
app.include_router(application_kits_router)
