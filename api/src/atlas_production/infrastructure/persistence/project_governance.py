from dataclasses import asdict
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.project_governance.records import ProjectRecord

from .base import OrmBase


class AtlasProjectRow(OrmBase):
    __tablename__ = "atlas_projects"

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    policy_profile_id: Mapped[str] = mapped_column(String, nullable=False)
