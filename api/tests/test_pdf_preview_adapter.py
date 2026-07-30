from __future__ import annotations

import hashlib
import inspect
from io import BytesIO
import os
import subprocess
import tempfile

import pikepdf
import pytest

from atlas_production.infrastructure import pdf_preview_adapter as preview_adapter
from atlas_production.infrastructure.pdf_preview_adapter import (
    PDF_PREVIEW_RENDERER_VERSION,
    PdfPreviewAdapter,
    PdfPreviewError,
)


_VALID_XMP = b"""<?xpacket begin="\xef\xbb\xbf"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>
</x:xmpmeta>
<?xpacket end="w"?>"""
_PAGE_CONTENT = b"BT /F1 12 Tf 20 150 Td (Atlas preview) Tj ET"
_HIDDEN_EMBEDDED_FILE = b"atlas-preview-must-not-leak-this-secret"


def unsafe_cyclic_pdf() -> bytes:
    name = pikepdf.Name
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(200, 300))
        page.CropBox = [10, 20, 190, 280]
        page.Rotate = 90
        page.Resources.Font = pikepdf.Dictionary(
            F1=pdf.make_indirect(pikepdf.Dictionary(
                Type=name.Font,
                Subtype=name.Type1,
                BaseFont=name.Helvetica,
            ))
        )
        page.Contents = pdf.make_stream(_PAGE_CONTENT)

        cycle = pdf.make_indirect(pikepdf.Dictionary())
        cycle[name("/Self")] = cycle
        page.Resources[name("/Cycle")] = cycle
        page.Resources[name("/AtlasSecret")] = pdf.make_stream(
            _HIDDEN_EMBEDDED_FILE,
            Type=name.EmbeddedFile,
        )
        deep_resource = pdf.make_indirect(pikepdf.Dictionary(Value="end"))
        for _ in range(1_200):
            deep_resource = pdf.make_indirect(
                pikepdf.Dictionary(Next=deep_resource)
            )
        page.Resources[name("/AtlasDeep")] = deep_resource
        page.obj[name.AA] = pikepdf.Dictionary(
            O=pikepdf.Dictionary(S=name.JavaScript, JS="app.alert('page')")
        )
        page.obj[name.Annots] = pikepdf.Array([
            pdf.make_indirect(pikepdf.Dictionary(
                Type=name.Annot,
                Subtype=name.Text,
                Rect=[0, 0, 10, 10],
                Contents="private note",
            ))
        ])
        page.obj[name.Metadata] = pdf.make_stream(_VALID_XMP)
        page.obj[name.PieceInfo] = pikepdf.Dictionary(
            Private=pikepdf.Dictionary(LastModified="D:20260716000000Z")
        )

        pdf.Root.OpenAction = pikepdf.Dictionary(
            S=name.JavaScript,
            JS="app.alert('document')",
        )
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([]))
        pdf.Root.Metadata = pdf.make_stream(_VALID_XMP)
        embedded = pdf.make_stream(b"attachment payload")
        file_spec = pdf.make_indirect(pikepdf.Dictionary(
            Type=name.Filespec,
            F="payload.txt",
            EF=pikepdf.Dictionary(F=embedded),
        ))
        pdf.Root.Names = pikepdf.Dictionary(
            EmbeddedFiles=pikepdf.Dictionary(
                Names=pikepdf.Array(["payload.txt", file_spec])
            )
        )
        pdf.docinfo["/Author"] = "Source Author"
        output = BytesIO()
        pdf.save(
            output,
            preserve_pdfa=False,
            fix_metadata_version=False,
            deterministic_id=True,
        )
        return output.getvalue()


def empty_password_encrypted_pdf(source: bytes) -> bytes:
    with pikepdf.Pdf.open(BytesIO(source)) as pdf:
        output = BytesIO()
        pdf.save(
            output,
            encryption=pikepdf.Encryption(
                owner="owner-password",
                user="",
                R=6,
            ),
        )
        return output.getvalue()


def blank_pdf(page_count: int) -> bytes:
    with pikepdf.Pdf.new() as pdf:
        for _ in range(page_count):
            pdf.add_blank_page(page_size=(200, 300))
        output = BytesIO()
        pdf.save(output, deterministic_id=True)
        return output.getvalue()


def render_all(
    content: bytes, *, max_pages: int = 3000
) -> tuple[int, list]:
    pages: list = []
    announced: list[int] = []
    with tempfile.TemporaryFile() as source:
        source.write(content)
        source.flush()
        source.seek(0)
        total = PdfPreviewAdapter(timeout_seconds=10).render_document(
            source,
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_pages=max_pages,
            on_document=announced.append,
            on_page=lambda _index, page: pages.append(page),
        )
    assert announced == [total]
    return total, pages


def test_cyclic_page_is_copied_as_deterministic_sanitized_single_page() -> None:
    source = unsafe_cyclic_pdf()

    first_total, first_pages = render_all(source)
    second_total, second_pages = render_all(source)
    first = first_pages[0]

    assert (first_total, second_total) == (1, 1)
    assert first.content == second_pages[0].content
    assert first.renderer_version == PDF_PREVIEW_RENDERER_VERSION
    assert first.media_box == (0.0, 0.0, 200.0, 300.0)
    assert first.crop_box == (10.0, 20.0, 190.0, 280.0)
    assert first.rotation == 90
    assert _HIDDEN_EMBEDDED_FILE not in first.content

    name = pikepdf.Name
    with pikepdf.Pdf.open(
        BytesIO(first.content),
        attempt_recovery=False,
        suppress_warnings=True,
    ) as preview:
        assert len(preview.pages) == 1
        assert set(preview.Root.keys()) == {name.Type, name.Pages}
        assert not preview.is_encrypted
        assert list(preview.docinfo.keys()) == []
        assert list(preview.attachments) == []
        page = preview.pages[0]
        assert list(page.MediaBox) == [0, 0, 200, 300]
        assert list(page.CropBox) == [10, 20, 190, 280]
        assert page.Rotate == 90
        assert page.Contents.read_bytes() == _PAGE_CONTENT
        assert set(page.Resources.keys()) == {name.Font}
        assert page.Resources.Font.F1.BaseFont == name.Helvetica
        assert not {
            name.Annots,
            name.AA,
            name.Metadata,
            name.PieceInfo,
            name.Thumb,
            name.AF,
        }.intersection(page.obj.keys())
        for obj in preview.objects:
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                assert name.Metadata not in obj
                assert name.PieceInfo not in obj
                assert name.AF not in obj
                assert obj.get(name.Type) != name.EmbeddedFile
                assert obj.get(name.S) != name.JavaScript
            if isinstance(obj, pikepdf.Stream):
                assert _HIDDEN_EMBEDDED_FILE not in obj.read_bytes()


def test_empty_password_encrypted_page_is_rendered_to_unencrypted_preview() -> None:
    _, pages = render_all(empty_password_encrypted_pdf(unsafe_cyclic_pdf()))
    rendered = pages[0]

    with pikepdf.Pdf.open(BytesIO(rendered.content)) as preview:
        assert len(preview.pages) == 1
        assert preview.is_encrypted is False
        assert list(preview.pages[0].MediaBox) == [0, 0, 200, 300]


def test_parser_and_capacity_failures_are_deterministic_preview_errors() -> None:
    with pytest.raises(PdfPreviewError, match="pdf_preview_source_invalid"):
        render_all(b"not a pdf")
    with pytest.raises(PdfPreviewError, match="artifact_too_large"):
        render_all(blank_pdf(3001), max_pages=3000)


def test_three_thousand_pages_stream_without_retaining_prior_page_results() -> None:
    content = blank_pdf(3000)
    announced: list[int] = []
    observed = 0

    def consume(page_index, page) -> None:
        nonlocal observed
        assert page_index == observed
        assert page.content.startswith(b"%PDF-")
        observed += 1

    with tempfile.TemporaryFile() as source:
        source.write(content)
        source.flush()
        source.seek(0)
        total = PdfPreviewAdapter(timeout_seconds=10).render_document(
            source,
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_pages=3000,
            on_document=announced.append,
            on_page=consume,
        )

    assert announced == [3000]
    assert total == observed == 3000


def test_one_document_uses_one_child_and_never_materializes_a_source_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_popen = subprocess.Popen
    children: list[object] = []

    def tracking_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(preview_adapter.subprocess, "Popen", tracking_popen)
    total, pages = render_all(blank_pdf(33))

    assert total == len(pages) == 33
    assert len(children) == 1
    implementation = inspect.getsource(PdfPreviewAdapter.render_document)
    assert "source.pdf" not in implementation
    assert "source.read(" not in implementation
    assert "pass_fds" in implementation


def test_frame_timeout_is_bounded_and_spawn_failure_stays_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "rb", closefd=False) as stream:
            with pytest.raises(TimeoutError):
                preview_adapter._read_exact(stream, 1, timeout_seconds=0)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    def spawn_failure(*_args, **_kwargs):
        raise OSError("spawn unavailable")

    monkeypatch.setattr(preview_adapter.subprocess, "Popen", spawn_failure)
    with tempfile.TemporaryFile() as source:
        content = unsafe_cyclic_pdf()
        source.write(content)
        source.seek(0)
        with pytest.raises(OSError, match="spawn unavailable"):
            PdfPreviewAdapter(timeout_seconds=1).render_document(
                source,
                expected_size=len(content),
                expected_sha256=hashlib.sha256(content).hexdigest(),
                max_pages=1,
                on_document=lambda _count: None,
                on_page=lambda _index, _page: None,
            )


@pytest.mark.parametrize(
    ("failure", "expected_returncode"),
    [
        (ImportError("pikepdf unavailable"), 21),
        (pikepdf.DependencyError("qpdf dependency unavailable"), 21),
        (RuntimeError("qpdf runtime unavailable"), 21),
        (OSError(27, "file too large"), 20),
    ],
)
def test_child_main_retries_import_and_runtime_infrastructure_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_returncode: int,
) -> None:
    monkeypatch.setattr(preview_adapter, "_child_limits", lambda: None)

    def fail_render(*_args, **_kwargs) -> None:
        raise failure

    monkeypatch.setattr(preview_adapter, "_render_document_child", fail_render)

    assert preview_adapter._main(
        [
            "pdf_preview_adapter.py",
            "--document",
            "0",
            "1",
            "0" * 64,
            "1",
        ]
    ) == expected_returncode
