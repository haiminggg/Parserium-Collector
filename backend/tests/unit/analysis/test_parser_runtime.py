import zipfile
from pathlib import Path

from parserium_collector.features.analysis.docx import LibreOfficeDocxConverter
from parserium_collector.features.analysis.parser import LiteParseAdapter

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
    <w:p><w:r><w:t>Parserium deterministic ruled table fixture</w:t></w:r></w:p>
    <w:tbl>
      <w:tblPr>
        <w:tblBorders>
          <w:top w:val="single" w:sz="8"/><w:left w:val="single" w:sz="8"/>
          <w:bottom w:val="single" w:sz="8"/><w:right w:val="single" w:sz="8"/>
          <w:insideH w:val="single" w:sz="8"/><w:insideV w:val="single" w:sz="8"/>
        </w:tblBorders>
      </w:tblPr>
      <w:tblGrid><w:gridCol w:w="3600"/><w:gridCol w:w="3600"/></w:tblGrid>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Fund</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>NAV</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Alpha</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>10.25</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


def write_member(archive: zipfile.ZipFile, name: str, contents: bytes) -> None:
    member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    archive.writestr(member, contents)


def write_ruled_table_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        write_member(archive, "[Content_Types].xml", CONTENT_TYPES_XML)
        write_member(archive, "_rels/.rels", PACKAGE_RELATIONSHIPS_XML)
        write_member(archive, "word/document.xml", DOCUMENT_XML)


def test_real_liteparse_extracts_ruled_table_and_renders_preview(tmp_path: Path) -> None:
    source = tmp_path / "ruled-table.docx"
    conversion_root = tmp_path / "conversion"
    conversion_root.mkdir()
    write_ruled_table_docx(source)
    converter = LibreOfficeDocxConverter(
        temporary_root=conversion_root,
        timeout_seconds=30,
    )
    converted = converter.convert(source)
    pdf = tmp_path / "ruled-table.pdf"
    pdf.write_bytes(converted.pdf_bytes)

    adapter = LiteParseAdapter(
        page_limit=10,
        parser_timeout_seconds=120,
        screenshot_dpi=150,
    )
    analysis = adapter.parse_pdf(pdf)

    assert analysis.page_count == 1
    assert analysis.analyzed_page_count == 1
    assert len(analysis.tables) == 1
    flattened = {cell for row in analysis.tables[0].cells for cell in row}
    assert {"Fund", "NAV", "Alpha", "10.25"} <= flattened
    preview = adapter.render_preview(pdf, page_num=analysis.preview_page_num)
    assert preview.width > 0
    assert preview.height > 0
    assert preview.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
