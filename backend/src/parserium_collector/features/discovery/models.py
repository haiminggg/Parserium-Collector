import re
from enum import StrEnum
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

DOMAIN_PATTERN = re.compile(
    r"(?=^.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class DocumentDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=30)
    document_types: tuple[DocumentType, ...] = (DocumentType.PDF, DocumentType.DOCX)
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()

    @field_validator("document_types")
    @classmethod
    def normalize_document_types(
        cls,
        value: tuple[DocumentType, ...],
    ) -> tuple[DocumentType, ...]:
        unique = tuple(dict.fromkeys(value))
        if not unique:
            raise ValueError("At least one document type is required.")
        return unique

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def normalize_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(domain.strip().lower() for domain in value))
        if len(normalized) > 20:
            raise ValueError("At most 20 domain hints are allowed.")
        if any(not DOMAIN_PATTERN.fullmatch(domain) for domain in normalized):
            raise ValueError("Each domain hint must be a hostname without a scheme or path.")
        return normalized

    @model_validator(mode="after")
    def domain_modes_are_exclusive(self) -> "DocumentDiscoveryRequest":
        if self.include_domains and self.exclude_domains:
            raise ValueError("include_domains and exclude_domains are mutually exclusive.")
        return self


class DocumentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    title: str | None = None
    description: str | None = None
    document_type: DocumentType
    source: Literal["firecrawl"] = "firecrawl"


class DocumentDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_search_ids: list[str]
    candidates: list[DocumentCandidate]
    rejected_non_document_results: int
