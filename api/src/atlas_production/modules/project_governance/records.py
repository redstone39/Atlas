from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    policy_profile_id: str
