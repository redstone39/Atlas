from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ResultSurface = Literal["conversation"]
GovernedStatus = Literal["answered", "unknown", "refused", "failed_closed"]


@dataclass(frozen=True)
class GovernedResult:
    status: GovernedStatus
    answer_text: str | None
    refusal_code: str | None
    user_reason: str
