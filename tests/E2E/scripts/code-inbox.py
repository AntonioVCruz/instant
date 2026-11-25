#!/usr/bin/env python3
"""Read-only local code inbox for one explicitly provisioned disposable account."""

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    email = config["email"]
    if not re.fullmatch(r"phase3-[a-f0-9]{32}@instant-e2e\.example", email):
        raise SystemExit("Only a disposable Phase 3 account is allowed")
    container = config["postgres_container"]
    if not re.fullmatch(r"instant-e2e(?:-[a-z0-9-]+)?-postgres-1", container):
        raise SystemExit("Unexpected database container")
    port = int(config.get("port", 13001))
    if not 1024 <= port <= 65535:
        raise SystemExit("Choose an unprivileged loopback port")

    def latest_code():
        # Source: instant.model.instant-user-magic-code. Read only this fixture
        # account's unconsumed code. No arbitrary email or SQL comes from HTTP.
        query = (
            "SELECT m.code FROM instant_user_magic_codes m "
            "JOIN instant_users u ON u.id=m.user_id "
            f"WHERE u.email='{email}' "
            "AND m.created_at > now()-interval '10 minutes' "
            "ORDER BY m.created_at DESC LIMIT 1;"
        )
        result = subprocess.run(
            ["docker", "exec", container, "psql", "-U", "instant", "-d", "instant",
             "-At", "-v", "ON_ERROR_STOP=1", "-c", query],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        if result.returncode:
            raise RuntimeError("Fixture inbox database check failed")
        code = result.stdout.strip()
        if code and not re.fullmatch(r"\d{6}", code):
            raise RuntimeError("Unexpected code format")
        return code

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                body = b'{"ready":true,"scope":"one disposable account"}'
                content_type = "application/json"
            elif self.path == "/code":
                try:
                    code = latest_code()
                except (RuntimeError, subprocess.TimeoutExpired):
                    self.send_error(503, "Local fixture inbox unavailable")
                    return
                refresh = '<meta http-equiv="refresh" content="2">' if not code else ""
                field = (
                    '<label for="verification-code">Verification code</label>'
                    f'<input id="verification-code" value="{code}" readonly>'
                    if code else "<p>Waiting for a sign-in code</p>"
                )
                body = (
                    '<!doctype html><html><head><meta charset="utf-8">'
                    f'{refresh}<title>Local test inbox</title></head><body>'
                    '<h1>Local test inbox</h1>'
                    f'<p>{html.escape(email)}</p>{field}'
                    '<p>This inbox is limited to one disposable local test account.</p>'
                    '</body></html>'
                ).encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            # Codes and request bodies never belong in ordinary log artifacts.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
