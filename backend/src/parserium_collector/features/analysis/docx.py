import os
import shutil
import signal
import subprocess  # noqa: S404 - executes one verified local binary without a shell
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from defusedxml import ElementTree


class DocxConversionError(RuntimeError):
    """A safe DOCX conversion failure suitable for worker classification."""


class DocxConversionTimeoutError(DocxConversionError):
    """The isolated LibreOffice process exceeded its wall-clock limit."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ConvertedPdf:
    pdf_bytes: bytes


class ProcessRuntime(Protocol):
    pid: int
    returncode: int | None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]: ...

    def kill(self) -> None: ...


CommandRunner = Callable[[list[str], Path, dict[str, str], float], ProcessResult]
MAX_RELATIONSHIP_XML_BYTES = 1024 * 1024


def run_bounded_command(
    command: Sequence[str],
    work_directory: Path,
    environment: dict[str, str],
    timeout_seconds: float,
    *,
    process_factory: Callable[..., ProcessRuntime] | None = None,
    kill_process_group: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    if timeout_seconds <= 0:
        raise ValueError("The conversion timeout must be positive.")
    factory = process_factory or cast(Callable[..., ProcessRuntime], subprocess.Popen)
    process = factory(  # noqa: S603 - command is a fixed argv list with no shell
        list(command),
        cwd=str(work_directory),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        if kill_process_group is not None:
            kill_process_group(process.pid, 9)
        elif os.name == "posix":
            posix_killpg = cast(Callable[[int, int], None], os.__dict__["killpg"])
            posix_killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        raise DocxConversionTimeoutError(
            "DOCX conversion exceeded the configured timeout."
        ) from error
    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
    )


class LibreOfficeDocxConverter:
    def __init__(
        self,
        *,
        temporary_root: Path,
        timeout_seconds: float,
        soffice_path: Path | None = None,
        command_runner: CommandRunner = run_bounded_command,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("The conversion timeout must be positive.")
        if not temporary_root.is_dir():
            raise ValueError("The conversion temporary root must exist and be a directory.")
        resolved_soffice = soffice_path
        if resolved_soffice is None:
            executable = shutil.which("soffice")
            if executable is None:
                raise ValueError("The LibreOffice conversion binary is unavailable.")
            resolved_soffice = Path(executable)
        if not resolved_soffice.is_absolute() or not resolved_soffice.is_file():
            raise ValueError("The LibreOffice conversion binary must be an existing absolute path.")
        self._temporary_root = temporary_root
        self._timeout_seconds = timeout_seconds
        self._soffice_path = resolved_soffice
        self._command_runner = command_runner

    def convert(self, source: Path) -> ConvertedPdf:
        if source.suffix.lower() != ".docx" or not source.is_file():
            raise DocxConversionError("DOCX conversion requires a validated DOCX file.")
        self._reject_active_content(source)
        try:
            with tempfile.TemporaryDirectory(
                prefix="parserium-docx-",
                dir=self._temporary_root,
            ) as temporary:
                workspace = Path(temporary)
                input_directory = workspace / "input"
                output_directory = workspace / "output"
                profile_directory = workspace / "profile"
                home_directory = workspace / "home"
                process_temp = workspace / "tmp"
                for directory in (
                    input_directory,
                    output_directory,
                    profile_directory,
                    home_directory,
                    process_temp,
                ):
                    directory.mkdir(mode=0o700)
                isolated_source = input_directory / "document.docx"
                shutil.copyfile(source, isolated_source)
                command = [
                    str(self._soffice_path),
                    "--headless",
                    "--safe-mode",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--norestore",
                    f"-env:UserInstallation={profile_directory.as_uri()}",
                    "--convert-to",
                    "pdf:writer_pdf_Export",
                    "--outdir",
                    str(output_directory),
                    str(isolated_source),
                ]
                environment = {
                    "HOME": str(home_directory),
                    "TMPDIR": str(process_temp),
                    "SAL_USE_VCLPLUGIN": "svp",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                }
                result = self._command_runner(
                    command,
                    workspace,
                    environment,
                    self._timeout_seconds,
                )
                if result.returncode != 0:
                    raise DocxConversionError("LibreOffice could not convert the DOCX file.")
                outputs = tuple(output_directory.glob("*.pdf"))
                if len(outputs) != 1:
                    raise DocxConversionError("DOCX conversion must produce exactly one PDF file.")
                pdf_bytes = outputs[0].read_bytes()
                if not pdf_bytes.startswith(b"%PDF-"):
                    raise DocxConversionError("DOCX conversion returned an invalid PDF file.")
                return ConvertedPdf(pdf_bytes=pdf_bytes)
        except (DocxConversionError, DocxConversionTimeoutError):
            raise
        except Exception as error:
            raise DocxConversionError("DOCX conversion failed safely.") from error

    @staticmethod
    def _reject_active_content(source: Path) -> None:
        try:
            with zipfile.ZipFile(source) as archive:
                names = {member.filename.casefold() for member in archive.infolist()}
                if any(
                    name.endswith("/vbaproject.bin")
                    or name.endswith("/vbadata.xml")
                    or name.startswith("word/activex/")
                    for name in names
                ):
                    raise DocxConversionError(
                        "DOCX files containing active content cannot be converted."
                    )
                content_types = archive.read("[Content_Types].xml").lower()
                if b"macroenabled" in content_types or b"vbaproject" in content_types:
                    raise DocxConversionError(
                        "DOCX files containing active content cannot be converted."
                    )
                LibreOfficeDocxConverter._reject_external_relationships(archive)
        except DocxConversionError:
            raise
        except Exception as error:
            raise DocxConversionError("The DOCX package could not be inspected safely.") from error

    @staticmethod
    def _reject_external_relationships(archive: zipfile.ZipFile) -> None:
        for member in archive.infolist():
            if not member.filename.casefold().endswith(".rels"):
                continue
            if member.file_size > MAX_RELATIONSHIP_XML_BYTES:
                raise DocxConversionError(
                    "The DOCX relationship metadata is too large to inspect safely."
                )
            relationship_xml = archive.read(member)
            lowered = relationship_xml.lower()
            if b"<!doctype" in lowered or b"<!entity" in lowered:
                raise DocxConversionError("The DOCX relationship metadata contains prohibited XML.")
            try:
                root = ElementTree.fromstring(relationship_xml)
            except ElementTree.ParseError as error:
                raise DocxConversionError("The DOCX relationship metadata is invalid.") from error
            for element in root.iter():
                for attribute_name, value in element.attrib.items():
                    local_name = attribute_name.rsplit("}", 1)[-1]
                    if local_name.casefold() == "targetmode" and value.casefold() == "external":
                        raise DocxConversionError(
                            "DOCX files containing external relationships cannot be converted."
                        )
