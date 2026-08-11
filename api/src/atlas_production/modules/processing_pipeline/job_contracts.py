from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict, TypeAlias

from atlas_production.shared.public import AuditEventRecord

ProcessingPublicStatusV1: TypeAlias = Literal[
    "queued",
    "processing",
    "waiting_retry",
    "publishing",
    "ready",
    "ready_with_warnings",
    "failed",
    "cancelled",
]



class ProcessingJobPayloadV1(TypedDict):
    document_id: str
    document_format: str
    profile_id: str | None
    profile_revision: int | None
    current_stage: str
    warning_codes: list[str]
    failure_code: str | None
    job_id: str
    status: ProcessingPublicStatusV1
    status_url: str
    audit_event_ref: NotRequired[str]
    retry_available: bool
    cancel_available: bool
    review_available: bool
    progress_current: int | None
    progress_total: int | None
    progress_unit: str | None
    elapsed_seconds: int
    attempt_started_at: str
    is_current: bool
    created_at: str
    updated_at: str


class ProcessingJobListResultV1(TypedDict):
    jobs: list[ProcessingJobPayloadV1]


ProcessingJobsResultV1: TypeAlias = (
    ProcessingJobPayloadV1 | ProcessingJobListResultV1
)




class ProcessingControlDenied(PermissionError):
    def __init__(self, reason: str, audit_event: AuditEventRecord):
        super().__init__(reason)
        self.reason = reason
        self.audit_event = audit_event


@dataclass(frozen=True, slots=True)
class ProcessingJobsFailureV1:
    error_code: str
    message_code: str
    status_code: int
    audit_event_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingJobsOutcomeV1:
    value: ProcessingJobsResultV1 | None = None
    status_code: int = 200
    failure: ProcessingJobsFailureV1 | None = None
