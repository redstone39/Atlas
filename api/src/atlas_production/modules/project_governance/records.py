from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    policy_profile_id: str
    status: Literal["active", "retired"]
