"""Model package — importing this registers every table on ``Base.metadata``.

``alembic/env.py`` and the test ``conftest.py`` import this module so the
metadata is fully populated before migrations / ``create_all`` run. As later
phases add entities (Resume, Job, …) drop a new module here and re-export it.
"""

from app.db.models.job import Job, JobChunk, JobEmbedding
from app.db.models.llm_call_log import LLMCallLog
from app.db.models.match import MatchResult
from app.db.models.resume import Resume, ResumeEmbedding, ResumeVersion
from app.db.models.skill_gap import SkillGapReport
from app.db.models.token import RefreshToken
from app.db.models.user import User

__all__ = [
    "Job",
    "JobChunk",
    "JobEmbedding",
    "LLMCallLog",
    "MatchResult",
    "RefreshToken",
    "Resume",
    "ResumeEmbedding",
    "ResumeVersion",
    "SkillGapReport",
    "User",
]
