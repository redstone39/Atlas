from atlas_production.modules.answer_behavior.public import AnswerBehaviorRevisionV1


class NullAnswerBehavior:
    def current(self) -> AnswerBehaviorRevisionV1:
        return AnswerBehaviorRevisionV1(
            revision=0,
            custom_guidance=None,
            guidance_digest=None,
            created_at=None,
        )

    def read_exact(
        self, *, revision: int, guidance_digest: str | None
    ) -> AnswerBehaviorRevisionV1:
        assert revision == 0
        assert guidance_digest is None
        return self.current()


__all__ = ["NullAnswerBehavior"]
