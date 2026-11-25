#!/usr/bin/env python3
"""Read-only typed-value oracle; never serves or prints fixture credentials."""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.request
import uuid


class Observer:
    def __init__(self, state_dir):
        root = Path(state_dir).resolve()
        if root.is_relative_to(Path(__file__).resolve().parents[3]):
            raise ValueError("Private state must be outside the repository")
        user = json.loads((root / "accounts.json").read_text(encoding="utf-8"))["A"]
        if not re.fullmatch(r"phase3-[a-f0-9]{32}@instant-e2e\.example", user["email"]):
            raise ValueError("Only a disposable fixture account is allowed")
        for key in ["app_id", "admin_token"]:
            if str(uuid.UUID(user[key])) != user[key]:
                raise ValueError("Invalid fixture identifier")
        self.headers = {"Content-Type": "application/json", "app-id": user["app_id"],
                        "Authorization": "Bearer " + user["admin_token"]}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(self):
        request = urllib.request.Request("http://127.0.0.1:18888/admin/query",
                                         data=b'{"query":{"e2e_types":{}}}', headers=self.headers)
        with self.opener.open(request, timeout=20) as response:
            rows = json.load(response)["e2e_types"]
        return [{"id": row["id"],
                 "number_zero": type(row.get("score")) in [int, float] and row["score"] == 0,
                 "boolean_false": row.get("active") is False,
                 "explicit_null": row.get("memo", "missing") is None}
                for row in rows if re.fullmatch(r"Typed[A-Za-z]{20}", row.get("title", ""))]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.duration <= 3600:
        parser.error("Duration must be between 1 and 3600 seconds")
    observer = Observer(args.state_dir)
    records = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        end = time.monotonic() + args.duration
        while time.monotonic() < end:
            for record in observer.read():
                if record not in records:
                    records.append(record)
            args.output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    raise SystemExit(0 if any(all(row[key] for key in ["number_zero", "boolean_false", "explicit_null"]) for row in records) else 1)
