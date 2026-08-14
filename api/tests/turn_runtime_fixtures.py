from atlas_production.modules.turn_runtime.public import (
    TurnRouteSnapshotV2,
    VisionRouteSnapshotV1,
)


def route_snapshot() -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id="test-route",
        route_revision=1,
        runtime_policy_revision=1,
        tokenizer_profile="cl100k_base",
        context_window_tokens=128000,
        max_input_tokens_per_invocation=112000,
        max_output_tokens_per_invocation=16000,
        max_tool_result_tokens_per_execution=16000,
        max_total_tokens_per_conversation=256000,
        vision_route=VisionRouteSnapshotV1(
            route_id="test-vision-route",
            route_revision=1,
            runtime_policy_revision=1,
            tokenizer_profile="cl100k_base",
            context_window_tokens=128000,
            max_input_tokens_per_invocation=112000,
            max_output_tokens_per_invocation=16000,
            max_tool_result_tokens_per_execution=16000,
            max_total_tokens_per_conversation=256000,
        ),
    )
