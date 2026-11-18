import os

# Needed so app.config / alembic env import without a real database.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:pw@localhost:5432/test")
