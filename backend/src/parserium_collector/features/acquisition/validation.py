import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree
from pypdf import PdfReader

from parserium_collector.features.acquisition.errors import (
    EncryptedDocumentError,
    InvalidDocumentError,
)
from parserium_collector.features.acquisition.models import DocumentType

CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
WORDPROCESSING_NAMESPACES = frozenset(
    (
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    )
)
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WINDOWS_DRIVE_MEMBER = re.compile(r"^[A-Za-z]:")
MAX_CONTENT_TYPES_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ValidatedDocument:
    document_type: DocumentType
    media_type: str


class DocumentValidator:
    def __init__(
        self,
        *,
        docx_max_expanded_bytes: int,
        docx_max_expansion_ratio: float,
    ) -> None:
        if docx_max_expanded_bytes < 1:
            raise ValueError("The DOCX expanded-size limit must be positive.")
        if docx_max_expansion_ratio <= 0:
            raise ValueError("The DOCX expansion-ratio limit must be positive.")
        self._docx_max_expanded_bytes = docx_max_expanded_bytes
        self._docx_max_expansion_ratio = docx_max_expansion_ratio

    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument:
        if expected_type is DocumentType.PDF:
            self._validate_pdf(path)
            return ValidatedDocument(
                document_type=DocumentType.PDF,
                media_type="application/pdf",
            )
        if expected_type is DocumentType.DOCX:
            self._validate_docx(path)
            return ValidatedDocument(
                document_type=DocumentType.DOCX,
                media_type=DOCX_MEDIA_TYPE,
            )
        raise InvalidDocumentError("The selected document type is not supported.")

    @staticmethod
    def _validate_pdf(path: Path) -> None:
        try:
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise InvalidDocumentError("The downloaded file is not a PDF document.")
            reader = PdfReader(path, strict=True)
            if reader.is_encrypted:
                raise EncryptedDocumentError("Encrypted PDF documents are not supported.")
            for page in reader.pages:
                _ = page.mediabox
        except (EncryptedDocumentError, InvalidDocumentError):
            raise
        except Exception as error:
            raise InvalidDocumentError("The PDF document is structurally invalid.") from error

    def _validate_docx(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                self._validate_docx_members(members)
                names = {member.filename for member in members}
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise InvalidDocumentError(
                        "The DOCX document is missing required Word package parts."
                    )
                self._validate_content_types(archive)
                self._validate_document_root(archive)
                if archive.testzip() is not None:
                    raise InvalidDocumentError("The DOCX document contains corrupt data.")
        except InvalidDocumentError:
            raise
        except Exception as error:
            raise InvalidDocumentError("The DOCX document is structurally invalid.") from error

    def _validate_docx_members(self, members: list[zipfile.ZipInfo]) -> None:
        seen_names: set[str] = set()
        total_expanded = 0
        total_compressed = 0
        for member in members:
            normalized_name = self._safe_member_name(member)
            identity = normalized_name.casefold()
            if identity in seen_names:
                raise InvalidDocumentError("The DOCX document contains duplicate members.")
            seen_names.add(identity)
            if member.flag_bits & 0x1:
                raise EncryptedDocumentError("Encrypted DOCX members are not supported.")
            if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise InvalidDocumentError(
                    "The DOCX document uses an unsupported compression method."
                )
            if member.is_dir():
                continue
            total_expanded += member.file_size
            total_compressed += member.compress_size
            if total_expanded > self._docx_max_expanded_bytes:
                raise InvalidDocumentError("The DOCX document exceeds the expanded-size limit.")
            if member.file_size / max(member.compress_size, 1) > self._docx_max_expansion_ratio:
                raise InvalidDocumentError("The DOCX document exceeds the compression-ratio limit.")
        if total_expanded / max(total_compressed, 1) > self._docx_max_expansion_ratio:
            raise InvalidDocumentError("The DOCX document exceeds the compression-ratio limit.")

    @staticmethod
    def _safe_member_name(member: zipfile.ZipInfo) -> str:
        name = member.filename[:-1] if member.is_dir() else member.filename
        mode = member.external_attr >> 16
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or name.startswith("/")
            or WINDOWS_DRIVE_MEMBER.match(name)
            or stat.S_ISLNK(mode)
        ):
            raise InvalidDocumentError("The DOCX document contains an unsafe member name.")
        parts = name.split("/")
        if any(part in {"", ".", ".."} or ":" in part for part in parts):
            raise InvalidDocumentError("The DOCX document contains an unsafe member name.")
        return "/".join(parts)

    @staticmethod
    def _validate_content_types(archive: zipfile.ZipFile) -> None:
        info = archive.getinfo("[Content_Types].xml")
        if info.file_size > MAX_CONTENT_TYPES_BYTES:
            raise InvalidDocumentError("The DOCX content-types part is too large.")
        content = archive.read(info)
        lowered = content.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise InvalidDocumentError("The DOCX content-types part contains prohibited XML.")
        root = ElementTree.fromstring(content)
        if root.tag != f"{{{CONTENT_TYPES_NAMESPACE}}}Types":
            raise InvalidDocumentError("The DOCX content-types part is invalid.")
        expected_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Override"
        if not any(
            element.tag == expected_tag
            and element.attrib.get("PartName") == "/word/document.xml"
            and element.attrib.get("ContentType") == DOCX_MAIN_CONTENT_TYPE
            for element in root
        ):
            raise InvalidDocumentError("The DOCX main document content type is invalid.")

    @staticmethod
    def _validate_document_root(archive: zipfile.ZipFile) -> None:
        with archive.open("word/document.xml") as stream:
            try:
                _, root = next(ElementTree.iterparse(stream, events=("start",)))
            except StopIteration as error:
                raise InvalidDocumentError("The DOCX main document part is empty.") from error
        valid_roots = {f"{{{namespace}}}document" for namespace in WORDPROCESSING_NAMESPACES}
        if root.tag not in valid_roots:
            raise InvalidDocumentError("The DOCX main document root is invalid.")
