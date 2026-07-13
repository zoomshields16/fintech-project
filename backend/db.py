# SQLAlchemy engine + session setup. SQLite file lives next to this module
# so `backend/app.db` is what you open in DB Browser for SQLite.

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Anchored to this file, not the working directory: a relative path would resolve
# against wherever the process was launched from, so running a script from the
# repo root would silently create a second, empty app.db there instead of finding
# this one.
DB_PATH = Path(__file__).resolve().parent / "app.db"

# DATABASE_URL lets a deploy point at Postgres without touching anything else here.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# check_same_thread is a SQLite-only flag (FastAPI serves sync endpoints from a
# threadpool, so connections cross threads). It is not a valid arg for Postgres.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
