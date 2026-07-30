from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .postgres_locks import advisory_lock_key


_STARTUP_LOCK_IDENTITY = "schema:atlas-production-bootstrap"


@dataclass(frozen=True, slots=True)
class PostgresRuntime:
    """Production database runtime with no business or projection state."""

    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def from_environment(cls) -> "PostgresRuntime":
        database_url = os.environ.get("ATLAS_PRODUCTION_DATABASE_URL")
        if not database_url:
            raise RuntimeError("ATLAS_PRODUCTION_DATABASE_URL is required")
        return cls.from_url(database_url)

    @classmethod
    def from_url(cls, database_url: str) -> "PostgresRuntime":
        parsed = make_url(database_url)
        if not parsed.drivername.startswith("postgresql"):
            raise ValueError("Production runtime requires a PostgreSQL database URL")
        engine = create_engine(database_url, pool_pre_ping=True)
        return cls(
            engine=engine,
            session_factory=sessionmaker(
                bind=engine,
                expire_on_commit=False,
                autoflush=False,
            ),
        )

    def bootstrap_schema(self) -> None:
        """Apply Alembic head while holding the startup-only lock on one connection."""

        lock_key = advisory_lock_key(_STARTUP_LOCK_IDENTITY)
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.commit()
            with connection.begin():
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )
                config = Config()
                config.set_main_option(
                    "script_location",
                    str(Path(__file__).resolve().parents[1] / "migrations"),
                )
                config.set_main_option(
                    "sqlalchemy.url",
                    "postgresql://bootstrap-via-existing-connection",
                )
                config.attributes["connection"] = connection
                command.upgrade(config, "head")


__all__ = ["PostgresRuntime"]
