"""Generate deterministic, test-only analysis fixtures."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

FIXTURE_ROOT = Path(__file__).resolve().parent
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _write_pdf(filename: str, content: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata(
        {
            "/Title": "Parserium deterministic analysis fixture",
            "/Producer": "Parserium test fixture generator",
        }
    )
    output = BytesIO()
    writer.write(output)
    (FIXTURE_ROOT / filename).write_bytes(output.getvalue())


def _table_text() -> bytes:
    return b"""BT
/F1 11 Tf
80 675 Td (Fund) Tj
170 0 Td (NAV USD) Tj
150 0 Td (Return) Tj
-320 -45 Td (Alpha Income) Tj
170 0 Td (10.25) Tj
150 0 Td (4.8 percent) Tj
-320 -45 Td (Beta Growth) Tj
170 0 Td (22.10) Tj
150 0 Td (7.2 percent) Tj
ET
"""


def generate_pdfs() -> None:
    ruled_lines = b"""q
0.8 w
70 705 m 540 705 l S
70 660 m 540 660 l S
70 615 m 540 615 l S
70 570 m 540 570 l S
70 570 m 70 705 l S
235 570 m 235 705 l S
390 570 m 390 705 l S
540 570 m 540 705 l S
Q
"""
    _write_pdf("ruled-table.pdf", ruled_lines + _table_text())
    _write_pdf("borderless-table.pdf", _table_text())
    _write_pdf(
        "no-table.pdf",
        b"BT /F1 13 Tf 72 700 Td (Quarterly investment commentary without tables.) Tj ET",
    )


def _zip_part(archive: ZipFile, name: str, value: bytes) -> None:
    member = ZipInfo(name, ZIP_TIMESTAMP)
    member.compress_type = ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    archive.writestr(member, value)


def generate_docx() -> None:
    content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    root_relationships = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Parserium deterministic investment table</w:t></w:r></w:p>
    <w:tbl>
      <w:tblPr><w:tblBorders>
        <w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>
        <w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>
        <w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>
      </w:tblBorders></w:tblPr>
      <w:tr><w:tc><w:p><w:r><w:t>Fund</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>NAV USD</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Return</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Alpha Income</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>10.25</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>4.8 percent</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Beta Growth</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>22.10</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>7.2 percent</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>
  </w:body>
</w:document>
"""
    target = FIXTURE_ROOT / "ruled-table.docx"
    with ZipFile(target, "w") as archive:
        _zip_part(archive, "[Content_Types].xml", content_types)
        _zip_part(archive, "_rels/.rels", root_relationships)
        _zip_part(archive, "word/document.xml", document)


if __name__ == "__main__":
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    generate_pdfs()
    generate_docx()
