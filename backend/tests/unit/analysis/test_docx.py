import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from parserium_collector.features.analysis.docx import (
    DocxConversionError,
    DocxConversionTimeoutError,
    LibreOfficeDocxConverter,
    ProcessResult,
    run_bounded_command,
)


def write_minimal_docx(
    path: Path,
    *,
    macro: bool = False,
    external_relationship: bool = False,
) -> None:
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Deterministic conversion fixture</w:t></w:r></w:p></w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        if macro:
            archive.writestr("word/vbaProject.bin", b"test-only macro fixture")
        if external_relationship:
            archive.writestr(
                "word/_rels/document.xml.rels",
                b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="https://documents.example/tracking.png" TargetMode="External"/>
</Relationships>
""",
            )


class SuccessfulConversionRunner:
    def __init__(self, *, output_count: int = 1) -> None:
        self.output_count = output_count
        self.commands: list[list[str]] = []
        self.work_directories: list[Path] = []
        self.environments: list[dict[str, str]] = []

    def __call__(
        self,
        command: list[str],
        work_directory: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ProcessResult:
        del timeout_seconds
        self.commands.append(command)
        self.work_directories.append(work_directory)
        self.environments.append(environment)
        output_directory = Path(command[command.index("--outdir") + 1])
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        for index in range(self.output_count):
            with (output_directory / f"converted-{index}.pdf").open("wb") as stream:
                writer.write(stream)
        return ProcessResult(returncode=0, stdout="converted", stderr="")


def test_converter_uses_isolated_profile_and_cleans_temporary_work(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    temporary_root = tmp_path / "conversion"
    temporary_root.mkdir()
    write_minimal_docx(source)
    runner = SuccessfulConversionRunner()
    converter = LibreOfficeDocxConverter(
        temporary_root=temporary_root,
        timeout_seconds=120,
        soffice_path=Path("/usr/bin/soffice"),
        command_runner=runner,
    )

    converted = converter.convert(source)

    assert converted.pdf_bytes.startswith(b"%PDF-")
    command = runner.commands[0]
    assert "--headless" in command
    assert "--safe-mode" in command
    assert "--norestore" in command
    assert "--convert-to" in command
    assert "pdf:writer_pdf_Export" in command
    profile_arguments = [value for value in command if value.startswith("-env:UserInstallation=")]
    assert len(profile_arguments) == 1
    assert profile_arguments[0].startswith("-env:UserInstallation=file://")
    assert runner.environments[0]["HOME"].startswith(str(temporary_root))
    assert runner.environments[0]["SAL_USE_VCLPLUGIN"] == "svp"
    assert list(temporary_root.iterdir()) == []


def test_converter_rejects_macro_parts_before_starting_libreoffice(tmp_path: Path) -> None:
    source = tmp_path / "macro.docx"
    temporary_root = tmp_path / "conversion"
    temporary_root.mkdir()
    write_minimal_docx(source, macro=True)
    runner = SuccessfulConversionRunner()
    converter = LibreOfficeDocxConverter(
        temporary_root=temporary_root,
        timeout_seconds=120,
        soffice_path=Path("/usr/bin/soffice"),
        command_runner=runner,
    )

    with pytest.raises(DocxConversionError, match="active content"):
        converter.convert(source)

    assert runner.commands == []


def test_converter_rejects_external_relationships_before_starting_libreoffice(
    tmp_path: Path,
) -> None:
    source = tmp_path / "external.docx"
    temporary_root = tmp_path / "conversion"
    temporary_root.mkdir()
    write_minimal_docx(source, external_relationship=True)
    runner = SuccessfulConversionRunner()
    converter = LibreOfficeDocxConverter(
        temporary_root=temporary_root,
        timeout_seconds=120,
        soffice_path=Path("/usr/bin/soffice"),
        command_runner=runner,
    )

    with pytest.raises(DocxConversionError, match="external relationships"):
        converter.convert(source)

    assert runner.commands == []


@pytest.mark.parametrize("output_count", (0, 2))
def test_converter_rejects_missing_or_ambiguous_pdf_output(
    tmp_path: Path,
    output_count: int,
) -> None:
    source = tmp_path / "report.docx"
    temporary_root = tmp_path / "conversion"
    temporary_root.mkdir()
    write_minimal_docx(source)
    converter = LibreOfficeDocxConverter(
        temporary_root=temporary_root,
        timeout_seconds=120,
        soffice_path=Path("/usr/bin/soffice"),
        command_runner=SuccessfulConversionRunner(output_count=output_count),
    )

    with pytest.raises(DocxConversionError, match="exactly one PDF"):
        converter.convert(source)


class HangingProcessTestDouble:
    pid = 4242
    returncode: int | None = None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd="soffice", timeout=timeout)
        self.returncode = -9
        return "", ""


def test_bounded_runner_kills_the_process_group_on_timeout(tmp_path: Path) -> None:
    process = HangingProcessTestDouble()
    killed: list[tuple[int, int]] = []

    def process_factory(*args: Any, **kwargs: Any) -> HangingProcessTestDouble:
        del args, kwargs
        return process

    with pytest.raises(DocxConversionTimeoutError):
        run_bounded_command(
            ["/usr/bin/soffice", "--headless"],
            tmp_path / "work",
            {"HOME": str(tmp_path / "home")},
            1,
            process_factory=process_factory,
            kill_process_group=lambda pid, signal_number: killed.append((pid, signal_number)),
        )

    assert killed == [(process.pid, 9)]
