#!/usr/bin/env python3
"""Check HTTP readiness and the dashboard's selected backend; no browser claims."""

import argparse
import json
import os
import sys
import urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP status at {url}: {response.status}")
        return response.read().decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    host = os.environ.get("INSTANT_E2E_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        parser.error("INSTANT_E2E_HOST must be a loopback hostname")
    backend = f"http://{host}:{os.environ.get('BACKEND_PORT', '18888')}"
    dashboard = f"http://{host}:{os.environ.get('DASHBOARD_PORT', '13000')}"
    storage = f"http://{host}:{os.environ.get('STORAGE_PORT', '19000')}"
    try:
        if json.loads(get(f"{backend}/health/system")) != {"wal": "ok"}:
            raise RuntimeError("Backend WAL health is not ok")
        html = get(f"{dashboard}/dash")
        if "__NEXT_DATA__" not in html and "self.__next_f" not in html:
            raise RuntimeError("Dashboard did not return a Next.js page")
        config = get(f"{dashboard}/publicSelfHostedVariables.js")
        if not config.startswith("window.__instantConfig = "):
            raise RuntimeError("Dashboard runtime configuration is missing")
        parsed = json.loads(config.split(" = ", 1)[1].strip().removesuffix(";"))
        if parsed != {"apiURI": backend}:
            raise RuntimeError("Dashboard points at an unexpected backend")
        get(f"{storage}/minio/health/live")
        if not args.quiet:
            print(json.dumps({"backend": backend, "dashboard": dashboard,
                              "storage": storage, "wal": "ok", "http_ready": True,
                              "runtime_config_matches": True,
                              "browser_rendering": "not verified"}))
        return 0
    except Exception as error:
        if not args.quiet:
            print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
