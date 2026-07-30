from __future__ import annotations

import argparse
import json

from atlas_production.infrastructure.composition import (
    build_artifact_storage_composition,
)
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.artifact_storage.records import (
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify an operator-provided artifact target and atomically bind it. "
            "Atlas services must already be stopped."
        )
    )
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--target-revision", required=True, type=int)
    parser.add_argument("--label", required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument(
        "--verification-mode",
        choices=("full_hash", "operator_accepted_unverified"),
        default="full_hash",
    )
    parser.add_argument(
        "--risk-acknowledgement",
        help=(
            "Required only for operator_accepted_unverified. Exact value: "
            f"{UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT}"
        ),
    )
    args = parser.parse_args()

    try:
        receipt = build_artifact_storage_composition().configure_offline_target(
            target_id=args.target_id,
            target_revision=args.target_revision,
            masked_label=args.label,
            operator_id=args.operator_id,
            change_id=args.change_id,
            verification_mode=args.verification_mode,
            risk_acknowledgement=args.risk_acknowledgement,
        )
    except ArtifactStorageError as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": exc.error_code,
                    "message_code": exc.message_code,
                    "message_params": exc.message_params,
                },
                sort_keys=True,
            )
        )
        return 1
    except (RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": "artifact_storage_target_configuration_rejected",
                    "message_code": str(exc),
                    "message_params": {},
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "succeeded",
                "operation_id": receipt["operation_id"],
                "committed_blob_count": receipt["committed_blob_count"],
                "total_bytes": receipt["total_bytes"],
                "blob_set_digest": receipt["blob_set_digest"],
                "storage_epoch": receipt["storage_epoch"],
                "verification_mode": receipt["verification_mode"],
                "evidence_claim": receipt["evidence_claim"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
