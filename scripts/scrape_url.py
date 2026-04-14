from __future__ import annotations

import argparse
import json
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="JSON with ScrapeRequest fields")
    parser.add_argument("--api", default="http://localhost:8000", help="Base API url")
    args = parser.parse_args()

    payload = json.loads(args.json)
    response = httpx.post(f"{args.api}/api/v1/scrape/page", json=payload, timeout=120)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
