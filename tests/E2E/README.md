# Self-hosted browser E2E environment

This harness builds Instant's backend and dashboard from the checkout using the
existing self-hosted Dockerfiles. The browser cases cover Explorer data/schema
integrity, filtering and relationship navigation, typed/required values,
destructive-action safeguards, and logout. The edit/persistence case passed five
consecutive runs before the shared-rule refactor. Expanded-suite execution is
recorded separately with exact source/harness hashes and run IDs.
The opt-in CI workflow is prepared; hosted execution is unverified. See
[CI decisions, activation and verification](CI.md).

Use Linux bash (including Ubuntu WSL), Docker with the Compose plugin, Python 3,
curl, git and flock. Allocate enough resources for Java, Node and database builds.
The application build happens inside Docker and does not touch a running Next.js
development server in the checkout.

For Windows checkouts, the installer compares source with Git's CRLF normalization
and creates LF-only Dockerfile copies in the private state directory. BuildKit
otherwise preserves CRLF in startup-script heredocs, producing invalid Linux
shebangs. The application checkout and its Dockerfiles remain unchanged.

Set a private state directory outside the checkout, then install:

```sh
export INSTANT_E2E_STATE_DIR=/path/outside/checkout/instant-e2e-runtime
export INSTANT_E2E_HOST=localhost
bash tests/E2E/scripts/install-instant.sh install
```

The installer generates credentials once, preserves them on repeat installation,
and pins the application source to HEAD in the private state file. Reusing state
after changing the source revision is rejected. Use a separate project and state
directory for another revision. Application images carry the source SHA label.
`install --no-build` reuses already-built, revision-labelled images.

The default Compose project is `instant-e2e`. To create another isolated instance,
set `INSTANT_E2E_PROJECT=instant-e2e-<suffix>` and a separate state directory.
Export unused DASHBOARD_PORT, BACKEND_PORT and STORAGE_PORT values before its first
installation. They are preserved in its private `.env` along with a unique volume
ownership ID. Never commit or paste that file into evidence.
`INSTANT_E2E_HOST` is also preserved; it changes the public dashboard/API/storage
origins while host bindings stay on `127.0.0.1`. The tunnel-tested profile uses
`localhost` for all three public origins.

| Service        | Default URL                 |
| -------------- | --------------------------- |
| Dashboard      | http://127.0.0.1:13000/dash |
| Backend        | http://127.0.0.1:18888      |
| Object storage | http://127.0.0.1:19000      |

Only these loopback ports are published. Postgres has no host port. Runtime
limits total 6 GB plus small initialization overhead; builds need additional
memory and disk. No shared VM, public ingress or TLS configuration is included.

```sh
bash tests/E2E/scripts/install-instant.sh status
bash tests/E2E/scripts/install-instant.sh ready
bash tests/E2E/scripts/install-instant.sh restart
bash tests/E2E/scripts/install-instant.sh logs server
bash tests/E2E/scripts/install-instant.sh stop
```

Readiness checks Postgres/WAL health through the backend, dashboard HTTP output,
the selected self-hosted backend in runtime configuration, and MinIO HTTP health.
These probes do not establish browser rendering, WebSocket synchronization,
login or testRigor tunnel compatibility. Startup polling is conditional and
bounded. A failed probe does not trigger an automatic destructive reinstall.

The operator account is `operator@instant-e2e.example`. With no email provider,
Instant prints login codes to the server's private logs. This is a supported local
setup path, not an automated login implementation. Treat logs as sensitive: they
can contain one-time codes. Do not publish them without reviewing and redacting
them. OAuth, billing and actual outbound email are outside this configuration.

## CLI localhost tunnel

From the machine that launches the CLI, check the exact target before a cloud
run. Bypass proxy environment variables and bound both connection and request
timeouts:

```sh
curl --noproxy '*' --connect-timeout 5 --max-time 20 \
  --fail --silent --show-error http://localhost:13000/dash
```

Use `http://localhost:13000/dash` as the CLI `--url` with `--localhost`.
CLI 3.0.0 passed the existing login-page capability probe with that URL on the
local Windows fixture. The same machine served both `127.0.0.1` and `localhost`
with HTTP 200 and identical dashboard HTML; an earlier tunneled `127.0.0.1` run
returned 502. This establishes a working configuration, not the cause of the
earlier failure or a guarantee that IP literals fail. The later five-run Explorer
journey also exercised authenticated editing and persistence through the tunnel.
It covered one pinned Chrome/local configuration.

Keep the existing case and label selection, supply `TESTRIGOR_API_KEY` through
the environment, and omit `--explicit-mutations` for ordinary runs. Use
`--verbose` to capture the allocated tunnel port, keep runs sequential, preserve
unique JUnit artifacts, and let the CLI close its tunnel. No HTTPS proxy was
needed for this result; HTTPS and certificate handling remain untested here.

Omitting `--explicit-mutations` did not prevent the observed CLI 3.0.0/server run
from retaining a supplied case. Treat both case and rule files as potential
remote updates and obtain approval for their exact scope. That flag remains
reserved for an explicitly approved authoritative replacement.

## Browser fixture and case scope

Prepare three separate disposable owners/apps for the expanded suite. This leaves
the previous fixture and operator account intact. The fixture tools never read
a testRigor credential. The expanded fixture currently targets the default
localhost ports and `instant-e2e-postgres-1` database container.

```sh
export INSTANT_E2E_FIXTURE_DIR=/private/path/instant-browser-fixture
python3 tests/E2E/scripts/fixture-expanded.py prepare \
  --state-dir "$INSTANT_E2E_FIXTURE_DIR"
python3 tests/E2E/scripts/code-inbox.py \
  --config "$INSTANT_E2E_FIXTURE_DIR/inbox-A.json"
# Start separate managed processes for inbox-B.json and inbox-C.json as well.
```

`prepare` prints a non-secret start URL and creates private account/app state,
three inbox configurations, typed/required/relationship schemas and a generated
`variables.json`. It preserves existing accounts and configured schemas. The
rename case consumes a fixture control row; teardown restores its namespace,
title and row ID with fresh schema/identity IDs because deletion reserves the
old schema IDs. The smaller `fixture-instant.py` remains available for single-account
probes and custom-origin profiles; it preserves private credentials and refuses
partial or retired state.

The inboxes bind only to `127.0.0.1` on ports 13001–13003. Each reads a six-digit, unconsumed code
for exactly the generated fake account through a read-only query to the fixture
database. Authentication then uses the real dashboard UI and API. HTTP requests
cannot choose another account, and the inbox does not expose admin/dashboard
tokens or serve private state files. Stop it after the run loop. The fake email
uses the existing `phase3-<random>@instant-e2e.example` convention; its prefix
does not select a test phase.

Run cases sequentially: each account/app/inbox is reused by several cases.
Confirm **Settings → Speed Optimizations → Allow parallel testing** is
disabled and automatic completed-run retries are disabled. A single Chrome
configuration does not establish that cases run sequentially. `settings.json`
versions the intended sequential profile (`useParallelCombinator: false`), as
used in testRigor's [documented CLI example](https://testrigor.com/blog/coolify-testing/).
The current loop omits `--settings-path` and uses the suite's manually confirmed
sequential setting. This file is not a full export of unexposed suite settings.
Before first applying it through the CLI, compare the suite settings before and
after in the app and obtain approval for that settings change.

| Case | Assertion that proves its behavior |
| --- | --- |
| TC-01 | Original and edited titles persist after reload; deletion persists after reload. |
| TC-02 | Filtering excludes another row and survives reload; both rows return after clearing. |
| TC-03 | Canceling deletion preserves the row after reload; confirming deletion removes it. |
| TC-04 | A seeded control row survives UI rename; deleting the renamed namespace removes it. |
| TC-05 | Following a relationship excludes unrelated destination rows; Explorer Back restores the original filter. |
| TC-06 | Forward/inverse links survive reload; unlinking preserves both records. |
| TC-07 | Numeric zero, boolean false and NULL remain distinct, including independent API type readback during execution. |
| TC-08 | Missing required data is rejected; valid data persists after reload. |
| TC-09 | A two-row deletion preserves an unselected control row. |
| TC-10 | Logout protects the previous Explorer page after navigation and reload. |

Filter checks combine exact result counts, matching data and exclusion of an
unmatched control, so text in the filter input cannot create a false pass.
The cancellation and required-field cases include successful controls. Write
cases generate unique titles or namespace names and remove their data through
the UI. The navigation-only case uses fixed, independent relationship controls
retained until app retirement. The rename case uses a separate seeded control
that teardown restores between runs. Nested rules share sign-in and Explorer readiness;
separate rules cover singular and plural destructive confirmation. Validate
cases with all four rules supplied, including the nested sign-in rule. New controls remain
source-grounded drafts until inspected in approved execution evidence.

Each case binds its account email, inbox URL and expected app title from the
generated variables file. The dashboard's normal fallback selects the signed-in
user's owned app while preserving the Explorer tab; the rule selects e2e_notes
after that redirect. Shared-app invitations are not used: this fixture's native
team/subscription app-list check excludes new collaborators from the free app.
No feature flags or authentication limits are changed to bypass that behavior.

Use forward slashes in CLI globs on Windows: the installed 3.0.0 parser matched
zero rule files for a backslash glob. Check parsed file counts before launch and
include nested rule labels in a label-filtered run. No `--explicit-mutations` or
settings file is needed for ordinary development runs.

The fixture's operations provide post-run verification and scoped failure cleanup:

```sh
python3 tests/E2E/scripts/fixture-expanded.py check --state-dir "$INSTANT_E2E_FIXTURE_DIR"
python3 tests/E2E/scripts/fixture-expanded.py clean --state-dir "$INSTANT_E2E_FIXTURE_DIR"
python3 tests/E2E/scripts/fixture-expanded.py revoke-sessions --state-dir "$INSTANT_E2E_FIXTURE_DIR"
```

A passing run must leave zero case-owned rows and temporary namespaces without
fallback cleanup. Four fixed navigation controls and the restored rename control
are intentional fixture data.
`clean` removes rows only in the five declared write namespaces and schema only
in generated `e2e_temp_` namespaces, within the three owned apps, after preserving
failed evidence.
`revoke-sessions` privately reads this fake user's session identifiers and revokes
them through `/dash/signout`, preserving the fixture bootstrap session. It does
not modify the database directly or touch the operator. `retire` deletes the
owned app through the dashboard API and revokes the bootstrap session; it leaves
the fake user record because no supported dashboard-user deletion route exists.

Run `observe-types.py --state-dir "$INSTANT_E2E_FIXTURE_DIR" --output "/private/run/typed-readback.json"`
concurrently with TC-07, starting before the
CLI. It reads only the disposable app's e2e_types rows and records type/value
booleans and row IDs. Require at least one record with all three checks true;
the UI case deletes the row before a post-run API check could inspect its types.
Stop the observer when the CLI finishes. It never reads the project PAT or
prints app credentials, and is bounded by its `--duration` argument.

Keep `state.json`, raw server logs and credentials out of artifacts. Supply only
the generated non-secret variables file to the CLI. The source default is 20
magic-code sends per email per hour; stop on rate-limit responses rather than
disabling the limit. Any case, rule, variable, setting, application or harness
change restarts the full-suite count. This development phase uses the user's
requested acceptance criterion of three consecutive complete suite passes.

## Reset

This environment is disposable. Reset clears **all database users/apps/data and
stored objects in this instance**. It preserves generated credentials, the
server's configuration/encryption keys, source images and other Docker projects.
It stops the project, checks ownership labels on the two data volumes, recreates
them and runs readiness again. It does not prune Docker or touch other volumes.

```sh
bash tests/E2E/scripts/install-instant.sh reset --confirm-reset instant-e2e
```

Use the actual project name for a suffixed instance. Export any data you want to
keep before resetting. Each eventual browser write case should also create its
own unique data and clean up through the product; full reset is a separate
environment operation.

## Storage build

The upstream Compose file's `minio/minio` and `minio/mc` references, their Quay
equivalents and the old binary archive were unavailable during setup. The harness
therefore builds official MinIO release commit
`9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a` and mc commit
`7394ce0dd2a80935aded936b09fa12cbb3cb8096`, retaining their license files.
The Go and Debian build bases and Postgres image are pinned by digest.

The backend uses the upstream Corretto 26 Dockerfile; record its resolved base and
the built image ID for each environment. Source tags, base digests, application
image IDs and harness file hashes belong in private run evidence. A published
`latest` image is not proof that the checkout was built.
