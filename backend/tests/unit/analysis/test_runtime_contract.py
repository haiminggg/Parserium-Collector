import os
import shutil
import subprocess  # noqa: S404 - the smoke test invokes a verified local binary directly
import zipfile
from importlib.metadata import version
from inspect import Parameter, signature
from pathlib import Path

from pypdf import PdfReader

CONTENT_TYPES_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
PACKAGE_RELATIONSHIPS_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""
DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Parserium deterministic DOCX conversion fixture</w:t></w:r></w:p>
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


def _write_member(archive: zipfile.ZipFile, name: str, contents: bytes) -> None:
    member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    archive.writestr(member, contents)


def _write_deterministic_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        _write_member(archive, "[Content_Types].xml", CONTENT_TYPES_XML)
        _write_member(archive, "_rels/.rels", PACKAGE_RELATIONSHIPS_XML)
        _write_member(archive, "word/document.xml", DOCUMENT_XML)


def test_liteparse_version_is_pinned() -> None:
    assert version("liteparse") == "2.14.0"


def test_liteparse_adapter_entry_points_match_verified_runtime() -> None:
    from liteparse import LiteParse

    assert tuple(signature(LiteParse.parse).parameters) == ("self", "file_data")

    screenshot_parameters = signature(LiteParse.screenshot).parameters
    assert tuple(screenshot_parameters) == ("self", "file_path", "page_numbers")
    assert screenshot_parameters["page_numbers"].kind is Parameter.KEYWORD_ONLY


def test_headless_libreoffice_converts_deterministic_docx_to_one_page_pdf(
    tmp_path: Path,
) -> None:
    soffice = shutil.which("soffice")
    assert soffice is not None, "The pinned backend image must provide soffice."

    source = tmp_path / "runtime-contract.docx"
    output_directory = tmp_path / "output"
    profile_directory = tmp_path / "profile"
    home_directory = tmp_path / "home"
    output_directory.mkdir()
    profile_directory.mkdir()
    home_directory.mkdir()
    _write_deterministic_docx(source)

    completed = subprocess.run(  # noqa: S603 - soffice is resolved without a shell
        [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            f"-env:UserInstallation={profile_directory.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_directory),
            str(source),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "HOME": str(home_directory)},
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    converted = output_directory / "runtime-contract.pdf"
    assert converted.is_file(), completed.stdout
    reader = PdfReader(converted)
    assert len(reader.pages) == 1
    assert "Parserium deterministic DOCX conversion fixture" in (
        reader.pages[0].extract_text() or ""
    )
