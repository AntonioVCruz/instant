#!/usr/bin/env python3
"""Provision and clean only an isolated local browser-test account and app."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check", "clean-rows", "revoke-sessions", "retire"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--backend", default="http://localhost:18888")
    parser.add_argument("--dashboard", default="http://localhost:13000")
    parser.add_argument("--postgres-container", default="instant-e2e-postgres-1")
    parser.add_argument("--inbox-port", type=int, default=13001)
    args = parser.parse_args()
    directory = args.state_dir.resolve()
    repository = Path(__file__).resolve().parents[3]
    if directory.is_relative_to(repository):
        parser.error("Keep private fixture state outside the repository")
    for url in [args.backend, args.dashboard]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.path or parsed.query or parsed.username or parsed.password or parsed.fragment:
            parser.error("Fixture origins must be plain loopback HTTP origins")
    if not re.fullmatch(r"instant-e2e(?:-[a-z0-9-]+)?-postgres-1", args.postgres_container):
        parser.error("Unexpected fixture database container")
    if not 1024 <= args.inbox_port <= 65535:
        parser.error("Use an unprivileged inbox port")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, data=None, headers=None, method=None):
        req = urllib.request.Request(
            args.backend + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})}, method=method,
        )
        try:
            with opener.open(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Fixture API HTTP {error.code} at {path}; response not logged") from None

    def query(sql):
        result = subprocess.run(
            ["docker", "exec", args.postgres_container, "psql", "-U", "instant", "-d", "instant",
             "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        if result.returncode:
            raise RuntimeError("Fixture database read failed; output not logged")
        return result.stdout.strip()

    def write(name, value):
        path = directory / name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    state_path = directory / "state.json"
    if (directory / "retired.json").exists():
        raise RuntimeError("This fixture was retired; prepare a new private state directory")
    if args.action == "prepare" and not state_path.exists():
        directory.mkdir(parents=True, exist_ok=False)
        request("/health/system")
        email = "phase3-" + uuid.uuid4().hex + "@instant-e2e.example"
        assert request("/dash/auth/send_magic_code", {"email": email})["sent"]
        code = query("SELECT m.code FROM instant_user_magic_codes m JOIN instant_users u ON u.id=m.user_id WHERE u.email='" + email + "' ORDER BY m.created_at DESC LIMIT 1;")
        if not re.fullmatch(r"\d{6}", code):
            raise RuntimeError("Disposable sign-in code not found")
        auth = request("/dash/auth/verify_magic_code", {"email": email, "code": code})
        state = {"email": email, "user_id": auth["user"]["id"], "dashboard_token": auth["token"],
                 "app_id": str(uuid.uuid4()), "admin_token": str(uuid.uuid4()), "namespace": "e2e_notes",
                 "backend": args.backend, "dashboard": args.dashboard, "postgres_container": args.postgres_container}
        # Persist private ownership before the app mutation so partial failures
        # remain inspectable. Never silently create a replacement on repeat use.
        write("state.json", state)
        request("/dash/apps", {"id": state["app_id"], "title": "Disposable Explorer suite", "admin_token": state["admin_token"]},
                {"Authorization": "Bearer " + state["dashboard_token"]})
        seed = str(uuid.uuid4())
        headers = {"app-id": state["app_id"], "Authorization": "Bearer " + state["admin_token"]}
        request("/admin/transact", {"steps": [["update", state["namespace"], seed, {"title": "schema-seed"}]]}, headers)
        request("/admin/transact", {"steps": [["delete", state["namespace"], seed]]}, headers)
        write("inbox.json", {"email": email, "postgres_container": args.postgres_container, "port": args.inbox_port})
        write("variables.json", {"e2eEmail": email, "codeInboxUrl": f"http://localhost:{args.inbox_port}/code", "namespace": state["namespace"]})
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not all((directory / name).is_file() for name in ["inbox.json", "variables.json"]):
        raise RuntimeError("Incomplete fixture state; inspect the existing directory before recovery")
    if not re.fullmatch(r"phase3-[a-f0-9]{32}@instant-e2e\.example", state["email"]) or state["namespace"] != "e2e_notes":
        raise RuntimeError("Refusing state outside the disposable fixture scope")
    for field in ["app_id", "admin_token", "dashboard_token", "user_id"]:
        if str(uuid.UUID(state[field])) != state[field]:
            raise RuntimeError("Invalid fixture identifier")
    if (state["backend"], state["dashboard"], state["postgres_container"]) != (args.backend, args.dashboard, args.postgres_container):
        raise RuntimeError("Fixture origins or database changed; use its original profile")
    headers = {"app-id": state["app_id"], "Authorization": "Bearer " + state["admin_token"]}
    rows = request("/admin/query", {"query": {state["namespace"]: {}}}, headers)[state["namespace"]]
    if args.action == "clean-rows" and rows:
        request("/admin/transact", {"steps": [["delete", state["namespace"], row["id"]] for row in rows]}, headers)
    revoked = 0
    if args.action in {"revoke-sessions", "retire"}:
        tokens = query("SELECT t.id FROM instant_user_refresh_tokens t JOIN instant_users u ON u.id=t.user_id WHERE u.id='" + state["user_id"] + "'::uuid AND u.email='" + state["email"] + "';")
        for token in tokens.splitlines():
            if str(uuid.UUID(token)) != token:
                raise RuntimeError("Unexpected disposable session identifier")
            if token != state["dashboard_token"]:
                request("/dash/signout", {}, {"Authorization": "Bearer " + token})
                revoked += 1
    if args.action == "retire":
        request("/dash/apps/" + state["app_id"], headers={"Authorization": "Bearer " + state["dashboard_token"]}, method="DELETE")
        request("/dash/signout", {}, {"Authorization": "Bearer " + state["dashboard_token"]})
        write("retired.json", {"app_deleted": True, "sessions_revoked": True, "user_record": "retained; no supported dashboard-user deletion endpoint"})
        print(json.dumps({"app_deleted": True, "sessions_revoked": True}))
        return
    remaining = request("/admin/query", {"query": {state["namespace"]: {}}}, headers)[state["namespace"]]
    print(json.dumps({"app_id": state["app_id"], "namespace": state["namespace"], "rows": len(remaining),
                      "rows_cleaned": len(rows) if args.action == "clean-rows" else 0, "sessions_revoked": revoked,
                      "start_url": args.dashboard + "/dash?" + urllib.parse.urlencode({"app": state["app_id"], "t": "explorer", "ns": state["namespace"]})}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
