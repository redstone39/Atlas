from dataclasses import asdict
from typing import Any

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.project_governance.records import ProjectRecord

from .base import OrmBase


class AtlasProjectRow(OrmBase):
    __tablename__ = "atlas_projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_atlas_projects_status",
        ),
    )

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    policy_profile_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)


class AtlasProjectCreateReceiptRow(OrmBase):
    __tablename__ = "atlas_project_create_receipts"
    __table_args__ = (
        UniqueConstraint(
            "scope_actor_id",
            "operation",
            "idempotency_key",
            name="uq_atlas_project_create_receipt_scope_operation_key",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    target_ref: Mapped[str] = mapped_column(String, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
