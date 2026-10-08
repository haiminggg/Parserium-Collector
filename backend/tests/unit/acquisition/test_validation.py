import warnings
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from parserium_collector.features.acquisition.errors import (
    EncryptedDocumentError,
    InvalidDocumentError,
)
from parserium_collector.features.acquisition.models import DocumentType
from parserium_collector.features.acquisition.validation import DocumentValidator

CONTENT_TYPES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p/></w:body>
</w:document>
"""


def write_pdf(path: Path, *, encrypted: bool = False) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("test-only-password")
    with path.open("wb") as stream:
        writer.write(stream)


def write_docx(
    path: Path,
    *,
    content_types: bytes = CONTENT_TYPES_XML,
    document_xml: bytes | None = DOCUMENT_XML,
    extras: tuple[tuple[str, bytes], ...] = (),
    duplicate_document: bool = False,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        if document_xml is not None:
            archive.writestr("word/document.xml", document_xml)
        for name, content in extras:
            archive.writestr(name, content)
        if duplicate_document:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("word/document.xml", DOCUMENT_XML)


def validator(
    *,
    max_expanded_bytes: int = 10 * 1024 * 1024,
    max_expansion_ratio: float = 100.0,
) -> DocumentValidator:
    return DocumentValidator(
        docx_max_expanded_bytes=max_expanded_bytes,
        docx_max_expansion_ratio=max_expansion_ratio,
    )


def test_valid_pdf_is_structurally_accepted(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    write_pdf(path)

    result = validator().validate(path, DocumentType.PDF)

    assert result.document_type is DocumentType.PDF
    assert result.media_type == "application/pdf"


def test_truncated_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\ntruncated")

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.PDF)


def test_encrypted_pdf_is_rejected_by_policy(tmp_path: Path) -> None:
    path = tmp_path / "encrypted.pdf"
    write_pdf(path, encrypted=True)

    with pytest.raises(EncryptedDocumentError):
        validator().validate(path, DocumentType.PDF)


def test_valid_minimal_docx_is_structurally_accepted(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_docx(path)

    result = validator().validate(path, DocumentType.DOCX)

    assert result.document_type is DocumentType.DOCX
    assert result.media_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_invalid_zip_is_rejected_as_docx(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    path.write_bytes(b"not a zip archive")

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.DOCX)


def test_docx_missing_word_document_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_docx(path, document_xml=None)

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.DOCX)


@pytest.mark.parametrize(
    "unsafe_name",
    (
        "../outside.xml",
        "/absolute.xml",
        "word\\other.xml",
        "C:/drive.xml",
    ),
)
def test_docx_unsafe_member_names_are_rejected(tmp_path: Path, unsafe_name: str) -> None:
    path = tmp_path / "report.docx"
    write_docx(path, extras=((unsafe_name, b"unsafe"),))

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.DOCX)


def test_docx_duplicate_member_names_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_docx(path, duplicate_document=True)

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.DOCX)


def test_docx_expanded_size_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_docx(path, extras=(("word/large.bin", b"x" * 4096),))

    with pytest.raises(InvalidDocumentError):
        validator(max_expanded_bytes=1024).validate(path, DocumentType.DOCX)


def test_docx_compression_ratio_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_docx(path, extras=(("word/compressed.bin", b"0" * 50_000),))

    with pytest.raises(InvalidDocumentError):
        validator(max_expansion_ratio=2.0).validate(path, DocumentType.DOCX)


def test_expected_pdf_rejects_docx_content(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    write_docx(path)

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.PDF)


def test_expected_docx_rejects_pdf_content(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    write_pdf(path)

    with pytest.raises(InvalidDocumentError):
        validator().validate(path, DocumentType.DOCX)
