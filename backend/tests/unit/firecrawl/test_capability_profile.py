from parserium_collector.adapters.firecrawl.capabilities import load_v2_11_162_profile


def test_v2_11_162_profile_disables_unproven_fetch_paths() -> None:
    profile = load_v2_11_162_profile()

    assert profile.release_tag == "v2.11.162"
    assert profile.git_sha == "7666c1f9ae8720a6bba271e0f60b6a217f8a5210"
    assert profile.capabilities["search.web.metadata_only"] == "untested"
    for name in (
        "search.web.with_scrape",
        "scrape",
        "crawl",
        "map",
        "extract",
    ):
        assert profile.capabilities[name] == "unsafe_disabled"
    assert profile.strict_searxng_only == "unsupported"
