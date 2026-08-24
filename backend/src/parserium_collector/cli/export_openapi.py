import argparse
import json
from pathlib import Path

from parserium_collector.main import create_app
from parserium_collector.settings import Settings


def render_contract() -> str:
    app = create_app(Settings(build_id="contract", release_version="0.1.0"))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_contract()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print("OpenAPI contract is stale.")
            return 1
        print("PASS: OpenAPI contract is current.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
