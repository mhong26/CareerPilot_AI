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

app = FastAPI(
    title="CareerPilot AI",
    version="0.1.0",
    description="AI-powered resume and job match platform",
)

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
