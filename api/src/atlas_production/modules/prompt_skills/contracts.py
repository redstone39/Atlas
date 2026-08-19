from __future__ import annotations


class PromptSkillError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message_code: str,
        status_code: int,
    ) -> None:
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code


__all__ = ["PromptSkillError"]
