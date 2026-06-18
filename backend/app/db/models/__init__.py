"""Model package — importing this registers every table on ``Base.metadata``.

``alembic/env.py`` and the test ``conftest.py`` import this module so the
metadata is fully populated before migrations / ``create_all`` run. As later
phases add entities (Resume, Job, …) drop a new module here and re-export it.
"""

from app.db.models.token import RefreshToken
from app.db.models.user import User

__all__ = ["RefreshToken", "User"]
