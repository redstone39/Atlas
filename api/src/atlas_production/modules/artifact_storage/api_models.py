from typing import Literal

from pydantic import BaseModel

from atlas_production.shared.user_messages import MessageReferenceModel


class ArtifactUploadAccepted(MessageReferenceModel):
    request_id: str
    status: Literal["accepted"] = "accepted"
    artifact_id: str
    job_id: str
    status_url: str
    target_ref: str
