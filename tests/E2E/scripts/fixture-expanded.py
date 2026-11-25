#!/usr/bin/env python3
"""Three isolated disposable owners/apps for the Explorer browser suite."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check", "clean", "revoke-sessions", "retire"])
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    repo = Path(__file__).resolve().parents[3]
    if root.is_relative_to(repo):
        parser.error("Private state must be outside the repository")
    root.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def write(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)

    def api(path, data=None, headers=None, method=None):
        request = urllib.request.Request(
            "http://127.0.0.1:18888" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})}, method=method,
        )
        try:
            with opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Local fixture HTTP {error.code} at {path}; response not logged") from None

    account_path = root / "accounts.json"
    if not account_path.exists():
        if args.action != "prepare":
            parser.error("Run prepare first")
        accounts = {}
        for key in "ABC":
            directory = root / key
            subprocess.run([sys.executable, str(Path(__file__).with_name("fixture-instant.py")),
                            "prepare", "--state-dir", str(directory)], check=True,
                           stdout=subprocess.DEVNULL, timeout=90)
            user = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            user["title"] = "Disposable Explorer suite"
            accounts[key] = user
            write(account_path, accounts)
        # Compatibility with the source-build development runner's owner pointer.
        write(root / "state.json", accounts["A"])
    accounts = json.loads(account_path.read_text(encoding="utf-8"))
    if set(accounts) != set("ABC"):
        raise RuntimeError("Partial account setup; inspect private state before recovery")
    for user in accounts.values():
        if not re.fullmatch(r"phase3-[a-f0-9]{32}@instant-e2e\.example", user["email"]):
            raise RuntimeError("Refusing non-disposable account state")
        for field in ["app_id", "admin_token", "dashboard_token", "user_id"]:
            if str(uuid.UUID(user[field])) != user[field]:
                raise RuntimeError("Invalid fixture identifier")

    def attr(ns, name, kind=None, required=False, index=False):
        result = {"id": str(uuid.uuid4()), "forward-identity": [str(uuid.uuid4()), ns, name],
                  "value-type": "blob", "cardinality": "one", "unique?": name == "id", "index?": index}
        if kind:
            result["checked-data-type"] = kind
        if required:
            result["required?"] = True
        return result

    def relation(child, parent):
        return {"id": str(uuid.uuid4()), "forward-identity": [str(uuid.uuid4()), child, "owner"],
                "reverse-identity": [str(uuid.uuid4()), parent, "notes"],
                "value-type": "ref", "cardinality": "one", "unique?": False, "index?": False}

    variables = {"namespace": "e2e_notes"}
    summary = []
    for index, (key, user) in enumerate(accounts.items()):
        headers = {"app-id": user["app_id"], "Authorization": "Bearer " + user["admin_token"]}
        if args.action == "prepare":
            schema_path = root / ("schema-" + key + ".json")
            if not schema_path.exists():
                # e2e_notes is created by the single-account preparation.
                attrs = []
                for ns in ["e2e_people", "e2e_related", "e2e_types", "e2e_required"]:
                    attrs.extend([attr(ns, "id"), attr(ns, "title", "string", ns == "e2e_required", ns == "e2e_people")])
                attrs.extend([attr("e2e_types", "score", "number"), attr("e2e_types", "active", "boolean"),
                              attr("e2e_types", "memo", "string"), relation("e2e_related", "e2e_people")])
                write(schema_path, attrs)
                api("/admin/transact", {"steps": [["add-attr", value] for value in attrs]}, headers)
            port = 13001 + index
            variables.update({"account" + key + "Email": user["email"], "account" + key + "Inbox": f"http://localhost:{port}/code",
                              "account" + key + "Title": user["title"], "account" + key + "Id": user["app_id"]})
            write(root / ("inbox-" + key + ".json"), {"email": user["email"], "postgres_container": "instant-e2e-postgres-1", "port": port})
        names = ["e2e_notes", "e2e_people", "e2e_related", "e2e_types", "e2e_required"]
        rows = api("/admin/query", {"query": {name: {} for name in names}}, headers)
        leftovers = {name: len(value) for name, value in rows.items() if value}
        schema = api("/admin/schema", headers=headers)["schema"]
        temporary = {name: value for name, value in schema["blobs"].items() if name.startswith("e2e_temp_")}
        if args.action == "clean":
            steps = [["delete", name, row["id"]] for name, values in rows.items() for row in values]
            steps.extend(["delete-attr", value["id"]] for values in temporary.values() for value in values.values())
            if steps:
                api("/admin/transact", {"steps": steps}, headers)
            remaining = api("/admin/query", {"query": {name: {} for name in names}}, headers)
            if any(remaining.values()):
                raise RuntimeError("Scoped row cleanup did not complete")
        revoked = 0
        if args.action in {"revoke-sessions", "retire"}:
            query = ("SELECT t.id FROM instant_user_refresh_tokens t JOIN instant_users u ON u.id=t.user_id WHERE u.id='"
                     + user["user_id"] + "'::uuid AND u.email='" + user["email"] + "';")
            result = subprocess.run(["docker", "exec", "instant-e2e-postgres-1", "psql", "-U", "instant", "-d", "instant",
                                     "-At", "-v", "ON_ERROR_STOP=1", "-c", query], capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise RuntimeError("Scoped session read failed; output not logged")
            for token in result.stdout.splitlines():
                if str(uuid.UUID(token)) != token:
                    raise RuntimeError("Unexpected session identifier")
                if token != user["dashboard_token"]:
                    api("/dash/signout", {}, {"Authorization": "Bearer " + token})
                    revoked += 1
        if args.action == "retire":
            api("/dash/apps/" + user["app_id"], headers={"Authorization": "Bearer " + user["dashboard_token"]}, method="DELETE")
            api("/dash/signout", {}, {"Authorization": "Bearer " + user["dashboard_token"]})
        summary.append({"account": key, "app_id": user["app_id"], "leftover_rows": leftovers,
                        "temporary_namespaces": list(temporary), "sessions_revoked": revoked})
    if args.action == "clean" and (root / "rename-seed.json").exists():
        owner = accounts["A"]
        owner_headers = {"app-id": owner["app_id"], "Authorization": "Bearer " + owner["admin_token"]}
        seed = json.loads((root / "rename-seed.json").read_text(encoding="utf-8"))
        schema = api("/admin/schema", headers=owner_headers)["schema"]
        if seed["namespace"] not in schema["blobs"]:
            fresh = [{**value, "id": str(uuid.uuid4()), "forward-identity": [str(uuid.uuid4()), seed["namespace"], value["forward-identity"][2]]} for value in seed["attrs"]]
            api("/admin/transact", {"steps": [["add-attr", value] for value in fresh]}, owner_headers)
        api("/admin/transact", {"steps": [["update", seed["namespace"], seed["row_id"], {"title": seed["title"]}]]}, owner_headers)
    if args.action == "check":
        owner = accounts["A"]
        owner_headers = {"app-id": owner["app_id"], "Authorization": "Bearer " + owner["admin_token"]}
        seed = json.loads((root / "rename-seed.json").read_text(encoding="utf-8"))
        rows = api("/admin/query", {"query": {seed["namespace"]: {}}}, owner_headers)[seed["namespace"]]
        if len(rows) != 1 or rows[0]["id"] != seed["row_id"] or rows[0]["title"] != seed["title"]:
            raise RuntimeError("Rename control is missing or changed; inspect before restoration")
    if args.action == "prepare":
        owner = accounts["A"]
        owner_headers = {"app-id": owner["app_id"], "Authorization": "Bearer " + owner["admin_token"]}
        rename_path = root / "rename-seed.json"
        if not rename_path.exists():
            write(rename_path, {"namespace": "e2e_rename_source", "title": "RenameControl" + uuid.uuid4().hex[:12],
                                "row_id": str(uuid.uuid4()), "attrs": [attr("e2e_rename_source", "id"), attr("e2e_rename_source", "title", "string")]})
        rename_seed = json.loads(rename_path.read_text(encoding="utf-8"))
        schema = api("/admin/schema", headers=owner_headers)["schema"]
        if rename_seed["namespace"] not in schema["blobs"]:
            # Deleted schema IDs remain reserved by the application's soft-delete
            # lifecycle. Preserve semantic fixture data and allocate fresh IDs.
            fresh = [{**value, "id": str(uuid.uuid4()), "forward-identity": [str(uuid.uuid4()), rename_seed["namespace"], value["forward-identity"][2]]} for value in rename_seed["attrs"]]
            api("/admin/transact", {"steps": [["add-attr", value] for value in fresh]}, owner_headers)
        api("/admin/transact", {"steps": [["update", rename_seed["namespace"], rename_seed["row_id"], {"title": rename_seed["title"]}]]}, owner_headers)
        variables.update({"renameNamespace": rename_seed["namespace"], "renameTitle": rename_seed["title"]})
        user = accounts["B"]
        headers = {"app-id": user["app_id"], "Authorization": "Bearer " + user["admin_token"]}
        path = root / "nav-seed.json"
        if not path.exists():
            attrs = [attr(ns, name, "string" if name == "title" else None, index=name == "title")
                     for ns in ["e2e_nav_people", "e2e_nav_related"] for name in ["id", "title"]]
            attrs.append(relation("e2e_nav_related", "e2e_nav_people"))
            seed = {"attrs": attrs, **{key: str(uuid.uuid4()) for key in ["p1", "p2", "c1", "c2"]}}
            write(path, seed)
            api("/admin/transact", {"steps": [["add-attr", value] for value in attrs]}, headers)
        seed = json.loads(path.read_text(encoding="utf-8"))
        api("/admin/transact", {"steps": [
            ["update", "e2e_nav_people", seed["p1"], {"title": "NavParentMatch"}],
            ["update", "e2e_nav_people", seed["p2"], {"title": "NavParentOther"}],
            ["update", "e2e_nav_related", seed["c1"], {"title": "NavChildMatch"}],
            ["update", "e2e_nav_related", seed["c2"], {"title": "NavChildOther"}],
            ["link", "e2e_nav_related", seed["c1"], {"owner": seed["p1"]}],
            ["link", "e2e_nav_related", seed["c2"], {"owner": seed["p2"]}]]}, headers)
        write(root / "variables.json", variables)
    if args.action == "check" and any(row["leftover_rows"] or row["temporary_namespaces"] for row in summary):
        raise RuntimeError("Case-owned rows or temporary namespaces remain; inspect before cleanup")
    print(json.dumps({"action": args.action, "accounts": summary,
                      "navigation_seed": "fixed read-only controls retained until app retirement",
                      "start_url": "http://localhost:13000/dash?t=explorer&ns=e2e_notes"}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
