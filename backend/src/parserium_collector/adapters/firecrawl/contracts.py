from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class MetadataSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def domain_modes_are_exclusive(self) -> "MetadataSearchRequest":
        if self.include_domains and self.exclude_domains:
            raise ValueError("include_domains and exclude_domains are mutually exclusive.")
        return self

    def firecrawl_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "query": self.query,
            "limit": self.limit,
            "sources": [{"type": "web"}],
            "highlights": False,
            "origin": "parserium-collector",
        }
        if self.include_domains:
            body["includeDomains"] = list(self.include_domains)
        if self.exclude_domains:
            body["excludeDomains"] = list(self.exclude_domains)
        return body


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    title: str | None = None
    description: str | None = None


class FirecrawlWebResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    title: str | None = None
    description: str | None = None
    position: int | None = None
    category: str | None = None


class FirecrawlSearchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web: list[FirecrawlWebResult] = Field(default_factory=list)


class FirecrawlSearchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    id: str
    data: FirecrawlSearchData
    credits_used: int = Field(default=0, alias="creditsUsed", ge=0)
    warning: str | None = None


class MetadataSearchResult(BaseModel):
    search_id: str
    results: list[SearchResult]


class FirecrawlProfile(BaseModel):
    profile_schema_version: int
    release_tag: str
    git_sha: str
    release_identity: str
    request_profile: str
    capabilities: dict[str, str]
    strict_searxng_only: str
    firecrawl_egress: str
    firecrawl_robots: str
    proof_artifacts: list[str]
