import json
from pathlib import Path

from parserium_collector.adapters.firecrawl.contracts import FirecrawlProfile


def load_v2_11_162_profile() -> FirecrawlProfile:
    path = Path(__file__).parent / "profiles" / "v2_11_162.json"
    return FirecrawlProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
