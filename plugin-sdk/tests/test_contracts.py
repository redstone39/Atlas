from datetime import datetime, timedelta, timezone
import unittest

from atlas_processing_sdk import CandidateDraft, ParserInput, RegionInput, SourceRegionDraft


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def parser_input(**overrides):
    values = {
        "run_id": "run", "invocation_id": "inv", "document_id": "doc",
        "document_version_id": "ver", "artifact_ref": "artifact:x",
        "media_type": "application/pdf", "profile_id": "profile",
        "profile_revision": 1, "policy_snapshot_ref": "policy:x",
        "deadline_at": NOW, "batch_id": "batch:1", "unit_start": 1,
        "unit_end": 1, "resume_cursor": None,
    }
    values.update(overrides)
    return ParserInput(**values)


class ContractTests(unittest.TestCase):
    def test_parser_input_requires_positive_revision(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            parser_input(profile_revision=0)

    def test_parser_input_requires_utc_deadline(self):
        with self.assertRaisesRegex(ValueError, "use UTC"):
            parser_input(deadline_at=datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=8))))

    def test_parser_input_requires_non_empty_one_based_inclusive_range(self):
        for field, value in (("unit_start", 0), ("unit_end", 0), ("unit_start", True)):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "one-based"):
                parser_input(**{field: value})
        with self.assertRaisesRegex(ValueError, "greater than or equal"):
            parser_input(unit_start=3, unit_end=2)
        with self.assertRaisesRegex(ValueError, "batch_id"):
            parser_input(batch_id=" ")

    def test_parser_input_resume_cursor_is_nullable_opaque_non_path_data(self):
        self.assertIsNone(parser_input().resume_cursor)
        self.assertEqual(parser_input(resume_cursor="cursor:next-2").resume_cursor, "cursor:next-2")
        for cursor in ("/tmp/cursor", "file:cursor", "https:cursor"):
            with self.subTest(cursor=cursor), self.assertRaisesRegex(ValueError, "opaque non-path"):
                parser_input(resume_cursor=cursor)

    def test_region_element_kind_hint_is_nullable_advisory_data(self):
        value = RegionInput("run", "inv", "doc", "ver", "artifact:x", "application/pdf", "profile", 1, "policy:x", NOW, "region", "page", "text", {"page": 1})
        self.assertIsNone(value.element_kind_hint)

    def test_drafts_require_replayable_locator_and_candidate_group_key(self):
        with self.assertRaisesRegex(ValueError, "locator"):
            SourceRegionDraft("page:1", "page", "text", {})
        candidate = CandidateDraft(("region:1",), "generic_text", "eir-draft-v1", "artifact:candidate")
        self.assertEqual(candidate.source_region_ids, ("region:1",))

    def test_candidate_preview_region_is_optional_and_bounded(self):
        region = {
            "page_number": 2,
            "region_kind": "paragraph",
            "source_element_id": "#/texts/1",
            "coordinate_system": "pdf_crop_box_relative_bottom_left",
            "rectangles": [[10.0, 20.0, 110.0, 60.0]],
            "page_width": 612.0,
            "page_height": 792.0,
            "geometry_version": "docling-page-region-v1",
        }
        candidate = CandidateDraft(
            ("region:1",), "generic_text", "eir-draft-v1", "artifact:candidate",
            preview_region=region,
        )
        self.assertEqual(candidate.preview_region, region)
        with self.assertRaisesRegex(ValueError, "outside"):
            CandidateDraft(
                ("region:1",), "generic_text", "eir-draft-v1", "artifact:candidate",
                preview_region={**region, "rectangles": [[10.0, 20.0, 700.0, 60.0]]},
            )


if __name__ == "__main__":
    unittest.main()
