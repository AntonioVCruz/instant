# Opt-in Explorer CI

Phase 5 implementation prepared on 2026-11-25. Hosted execution is **unverified**.
The user subsequently authorized a PR against their fork to test this workflow.
That activation adds owner-authored same-repository PR runs alongside manual
dispatch; it does not authorize an upstream PR or a merge.
The Phase 4 evidence remains three local-tunnel full-suite passes (30/30), against
application source `15358ad712df6879b8953ee8289af0e891d4dde8`. No new browser run,
remote suite update, repository configuration, commit, push or publication was
performed for Phase 5. Changes to this harness do not extend the historical streak.

The workflow is [e2e-tests.yml](../../.github/workflows/e2e-tests.yml). It builds
the checked-out backend/dashboard with the existing installer, prepares three
disposable owners/apps, and runs all ten versioned cases and four nested rules
using the CLI localhost tunnel. It starts all three loopback login inboxes and
the concurrent typed-value observer. API checks require zero write rows and
temporary namespaces, deletion of the rename namespace, and session deltas
`A=3, B=3, C=3` (nine logins retained; TC10's login is revoked by logout).
The intentional rename control is restored only after the zero-leftover check.
No fallback cleanup can make a failed run pass.

## Repository decisions

| Decision | Implementation and reason |
| --- | --- |
| Trigger | Default-branch manual dispatch, or a non-draft PR authored by the repository owner from the same repository to its default branch. PR paths cover this workflow, tests/E2E, client and server. This allows the owner's requested fork CI experiment before merging. No schedule or push trigger. |
| Opt-in gate | Missing/empty `TESTRIGOR_CI_TOKEN`, absent/invalid `TESTRIGOR_SUITE_ID`, fork-originated PRs, non-owner PRs, drafts and other events/refs produce a successful gate and skipped E2E job. No `pull_request_target`. A fork can opt in independently with its own configuration. |
| Check policy | Advisory for merging: do not add it as a required branch-protection check. An opted-in execution fails on build, CLI, JUnit, oracle or cleanup failure; failures are visible, not hidden by `continue-on-error`. |
| Runner | Ephemeral GitHub-hosted `ubuntu-24.04`, Docker/Compose, Node 22.15.0, Python stdlib. Preflight requires 12 GiB RAM and 25 GiB free disk. Runtime container caps total about 6 GiB; builds need additional capacity. Smaller private-repository runners may fail preflight and need a larger runner. No existing self-hosted machine is used. |
| Duration | Hard job limit 180 minutes; build/prepare limit 100 minutes, synchronous suite step limit 70 minutes, cleanup limit 8 minutes. These are initial budgets, not measured hosted timings. No automatic retries. Hard timeout/manual cancellation can interrupt a remote run: inspect its status before any later local or CI execution. |
| Cloud access | CLI `3.0.0` with `--localhost`, public origin `http://localhost:13000`, backend 18888 and inboxes 13001–13003. Direct curl and installer readiness precede the cloud call. This topology worked locally; hosted tunnel reachability and resource sufficiency still need the first approved hosted run. |
| Credentials | Secret `TESTRIGOR_CI_TOKEN` maps to `TESTRIGOR_API_KEY` only on the run step (plus gate presence detection). Never passed as a command argument. `TESTRIGOR_SUITE_ID` is a repository variable. For the owner's fork experiment, use the designated private Instant credential through stdin to GitHub's encrypted secret interface; never commit it. |
| Suite scope | Versioned UUID-pinned cases and named rules, filtered by `instant-explorer` plus all nested rule labels. The current UUIDs belong to development suite `wa2gc5bmGhWWTyx8M`. Files can update saved objects even without `--explicit-mutations`; dispatch authorizes this exact ten-case/four-rule scope. No settings file, globals or authoritative replacement. Manually verify sequential testing and completed-run retries disabled before activation. |
| Serialization | Stable suite-specific concurrency, `cancel-in-progress: false`, across all branches of this repository. GitHub concurrency does not coordinate other repositories or local CLI runs, and pending runs are not a durable FIFO. Reserve the suite across all callers before dispatch. |
| Permissions/provenance | `contents: read`, checkout credential persistence disabled, actions pinned to verified full SHAs. Branch/commit supplied through environment variables. Artifacts record source/harness SHA, file hashes and built image IDs/digests; no mutable release image substitutes for building the checkout. |
| Evidence | Unique run-ID/attempt artifact, seven-day retention. Reconstructed JUnit contains outcomes only; step text/system output are withheld. Type/session/cleanup oracles, run URLs and provenance are safe exports. Failure container logs export only fixed event classifications/counts; all original message bodies are withheld to exclude unknown credential formats. Raw CLI/build/container logs, OTPs, generated variables and fixture state are never uploaded. Raw logs exist only in private ephemeral runner directories. Use the testRigor run for detailed failure triage. |
| Cleanup | Inbox/observer processes stop in `finally`; an `always()` step revokes browser sessions and removes this ephemeral Compose project's containers and volumes. Cleanup errors fail the job without masking the run step's failure. Never invokes host-wide prune or resets the existing workstation fixture. Runner destruction removes private state. |

## Activation and ownership

Before activation, the repository owner must designate the suite owner, CI token
holder, rotation interval/expiry, backup owner and triage contact. These remain
open maintainer decisions; no ownership agreement is implied by this draft.
An upstream integration should consider a separate CI suite. The current
UUID-pinned files cannot be silently pointed at a new suite: first provision it,
review identities/rules/settings and validate its bindings in a separately
approved migration. Cross-repository runs must never share a suite accidentally.

For a reviewed activation on the existing designated suite:

1. Review the workflow and harness diff, service dependency and suite data visibility.
2. Confirm the suite is idle, owns these ten UUIDs/four rules, uses the approved
   Chrome profile, has parallel testing and completed-run retries disabled, and
   has no unrelated cases with `instant-explorer` labels.
3. Publish through the approved repository route. Owner-authored same-repository
   PRs run before merging; external fork PRs are excluded. Manual dispatch still
   requires the default branch.
4. Have the token holder configure repository secret `TESTRIGOR_CI_TOKEN` and
   variable `TESTRIGOR_SUITE_ID`, then approve one manual hosted run. Do not copy a
   PAT into repository files or expose it in logs. A suite CI token is preferred
   for CI and does not grant MCP access. Validate token compatibility in that run.
5. Inspect CLI/JUnit, independent oracles, tunnel/run links, image provenance,
   cleanup and actual duration. Classify failures before retrying. On 502, rate
   limits or secret exposure, stop and report; do not launch a workaround run.

The Phase 5 playbook explicitly requires confirmation before pushing, configuring
remote secrets/variables, enabling automation or starting a hosted run. Those
actions were not included in local preparation. The official
[testRigor CLI reference](https://testrigor.com/command-line/) was checked, along
with the installed 3.0.0 help and token resolution code; the workflow uses the
verified environment-variable interface rather than copying older flag examples.

For rotation, replace the secret after validating a new token against the same
suite, then revoke the old token in testRigor. On failure, the named triage owner
checks the Actions step and sanitized artifact, then the remote run's failing
step; distinguish installer/tunnel/fixture/test/application causes. Do not start
another run while a remote run is active. For removal, disable the workflow,
remove the secret/variable, revoke its token and remove any required-check rule
if one was added later. Retain or retire the suite according to its owner's
decision; do not delete the shared development suite automatically.

## Local verification and continuation

Run the offline lifecycle regressions with:

```sh
python3 tests/E2E/scripts/test-ci-run.py -v
actionlint .github/workflows/e2e-tests.yml
```

The regression checks cover ten-result acceptance, typed/logout/cleanup rejection,
preserving a nonzero CLI exit when JUnit is missing, safe JUnit/log exports,
private-directory boundaries, and attempted teardown after session cleanup fails.
The workflow gate is exercised offline for fourteen scenarios covering missing
configuration, manual refs, owner PRs, external forks, other authors, drafts,
non-default PR bases and privileged PR events.
These checks do not build or run the application and cannot prove hosted E2E.

Verification on 2026-11-25: actionlint **1.7.12** passed; **eight** lifecycle
regression tests passed (the gate test now contains fourteen scenarios); all E2E Python
files parsed; installer `bash -n` passed; workflow structural checks and
`git diff --check` passed. Action release refs were resolved directly from the
official repositories; the actionlint binary matched its release SHA-256 digest.

Current checkout remains on `main`; application source is unchanged. All E2E
files and this workflow are uncommitted. Next action is review/approved activation,
followed by recording the first hosted result. Phase 6 contribution packaging
and upstream publication remain separate.
