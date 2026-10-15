# Enterprise Readiness Plan

A tracked, sequenced plan for closing the gaps in `docs/PRODUCTION_CERTIFICATION.md` and
`docs/THREAT_MODEL.md`. Check items off as they're actually verified — not when code is
written, when the evidence exists. Update this file in the same commit as the work it
tracks, the same discipline every Master Plan phase in this repo has followed.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done, with evidence linked.

## Phase A — Prove it works for real (no new code)

Nothing else on this list matters until this phase is done - every later phase either
builds on these systems or needs their own real behavior verified, not a fake's.

- [x] **A.1** Real Postgres + Redis running, full backend suite re-run against them.
      Docker Desktop installed (needed WSL2 - `wsl --install`, a reboot, and a fresh
      `docker compose up -d postgres redis`, all done interactively with the user). Real
      Postgres 16 + Redis 7 containers healthy. `backend/tests/` (280 tests, SQLite/fakeredis
      by design) still passes unaffected. `tests/integration/` (real Postgres+Redis, 4
      tests) run for the first time in this project's history - found and fixed two real
      bugs to get there, see the Log.
- [x] **A.2** Repo pushed to a new private GitHub repo
      ([munawazus/ocpcc](https://github.com/munawazus/ocpcc)); CI ran to completion on a
      real GitHub Actions runner for the first time ever - both jobs green, including
      "Integration tests (real Postgres + Redis)" on a fresh Linux runner (independent
      confirmation of A.1's local fixes, on a different OS). Found and fixed one more real
      bug along the way, see the Log.
- [x] **A.3** Playwright browsers installed; `frontend/e2e/login-flow.spec.ts` executes for
      the first time ever. Found and fixed one real bug along the way, see the Log.
- [~] **A.4** Real Oracle connection kept running for days (not minutes) - collector
      survives real restarts, real drift, real intermittent network issues. Connected,
      app-restart and network-outage resilience both verified with real evidence, one real
      bug found and fixed; the multi-day observation window itself is still running - see
      the Log.
- [x] **A.5** Real SMTP relay configured; one real HIGH/CRITICAL incident email delivered.
      Real Gmail relay, real recipient mailbox, two real emails confirmed received (HIGH and
      a CRITICAL escalation). See the Log.
- [x] **A.6** Real local LLM configured (`AI_ENABLED=true`); one real Copilot query and one
      real RCA investigation completed, including firing the adversarial payload from
      `test_adversarial_qa.py` at the real model instead of the fake one. Found and fixed a
      real gap (RCA JSON-shape adherence), one real gap left honestly open (model schema
      drift on complex evidence - the code already handles it safely). See the Log.

## Phase B — Re-certify

- [x] **B.1** Re-run `docs/PRODUCTION_CERTIFICATION.md`'s full G0-G11 gate review against
      Phase A's evidence. Verdict moved from NOT CERTIFIED to CERTIFIED-with-3-tracked-
      exceptions (G1, G7, G9 still PARTIAL). See the Log.
- [x] **B.2** UI control-inventory audit (master plan Sec 34) - every interactive control
      enumerated, each with a defined behavior and a test (no infra dependency - can run in
      parallel with Phase A). Found and fixed several real, previously-zero-coverage gaps
      (most notably `DatabasesPage`, the core CRUD surface); several lower-value gaps
      remain, tracked honestly rather than hidden. See `docs/UI_CONTROL_INVENTORY.md` and
      the Log.

## Phase C — Security hardening for a procurement/vendor review

- [x] **C.1** SSRF: pin the actually-connected socket's IP (custom `httpx` transport), not
      just a resolve-then-check. Closes webhook delivery's DNS-rebinding gap; Oracle/SMTP
      host fields remain unprotected (different protocols, out of scope for an httpx
      transport). See the Log.
- [x] **C.2** Body-size limit enforced on actual streamed bytes, not just `Content-Length`.
      Found and fixed a real bug of my own along the way (FastAPI's body-parsing swallows
      any non-`HTTPException` into a generic 400). See the Log.
- [x] **C.3** Decide multi-tenancy model before Phase D starts building more on top of the
      current assumption: single deployment per customer, or one deployment serving many?
      **Decided: single deployment per customer, permanently** - no `organization_id`/
      query-scoping work planned. See the Log.

## Phase D — Feature completeness (master plan phases never built)

- [x] **D.1** SQL Intelligence (master plan Phase 9) - top SQL, regression detection, plan
      history. The single biggest gap for DBA credibility. No master-plan spec document
      exists for this (confirmed absent from the repo and this machine) - designed fresh,
      approved by the user, and built end-to-end: real collector, repository, analytics, API,
      AI tools, de-stubbed reports/health-check, and a real frontend page. See the Log.
- [x] **D.2** Capacity/tablespace collector (referenced by master plan Phase 5's own scope,
      never implemented) - closes the Capacity report/dashboard's `data_available: false`.
      Same no-spec-exists situation as D.1 - designed fresh, mirroring D.1's architecture,
      and built end-to-end: real collector, repository, analytics (growth-rate projection +
      deterministic alerting), API, AI tool, de-stubbed reports/health-check, and a real
      frontend page. See the Log.
- [x] **D.3** Statspack integration (master plan Phase 11) - a materially different shape
      from D.1/D.2: this platform never creates Statspack snapshots, only detects and reads
      what the DBA's own independent statspack.snap job already produced. Designed fresh (no
      spec exists for this either), built end-to-end: strengthened detection, incremental
      sync, reused D.1's own delta logic, API, on-demand AI tool, de-stubbed Daily DBA
      section, and a real UI panel. See the Log.
- [x] **D.4** Change Intelligence (master plan Phase 14) - configuration parameter drift
      detection from `V$SYSTEM_PARAMETER`, built end-to-end: strengthened allowlist, a new
      collector, a set-diff analytics module (no delta machinery reused from D.1 - the one
      place this platform's own analytics didn't fit), API, on-demand AI tool, de-stubbed
      Daily DBA section and the `"configuration"` health-check category, and a real dedicated
      frontend page. Fully live-verified end-to-end against orclpdb (unlike D.3). See the Log.

## Phase E — AI maturity

- [x] **E.1** RCA quality benchmark suite (master plan Phase 20) - a 5-scenario gold-incident
      set, 5 deterministic hallucination/uncertainty checks, and a real run against the real
      `qwen2.5:7b-instruct` model this session had reachable - **1 of 5 scenarios passed every
      check**: no hallucination or overclaiming anywhere, but the model declined to name a
      root cause on all 4 clear-evidence scenarios. See the Log and
      `docs/RCA_BENCHMARK_REPORT.md`.

## Phase F — QA hardening (found by an intensive live QA pass, not a design gap)

Not master-plan phases - real defects an adversarial black-box + code-level QA pass found
against the live, real-Postgres/real-Oracle/real-Ollama stack after D.1-D.4/E.1 shipped.
Each item gets its own EnterPlanMode→implement→test→live-verify→commit cycle, same
discipline as every phase above, specifically so a regression test proves the fix rather
than just the report claiming one.

- [x] **F.1** (critical) Every Oracle connection attempt (5 collectors in
      `collector_service.py` + 2 in `oracle_capability_service.py`, 7 call sites total) calls
      synchronous, blocking `oracledb.connect()` directly inside `async def` code, never
      offloaded via `asyncio.to_thread`. Proven live: registering databases with unreachable
      hosts made a trivial, unrelated `GET /api/health` call take 3.1s and 8.0s (vs. 2-32ms
      normal) - the *entire app freezes for every user*, not just the affected database,
      for the cumulative duration of every slow/unreachable connection attempt in the fleet,
      every collector interval. Invisible to Phase 25's own scale benchmark, which uses an
      instantly-returning fake connector. Fixed at all 7 sites, plus every query/close call
      after connect (not just connect itself). See the Log.
- [x] **F.2** (minor) `AuthProvider`'s mount-time `/api/auth/refresh` call wasn't deduplicated
      the way `lib/api.ts`'s retry-triggered refresh already was (its own `refreshInFlight`
      singleton, previously private to that one call site). Moved the dedup guard into a
      newly-exported `refreshAccessToken()` so every caller shares it. Live-verified this
      closes the exact trigger that actually fires on every dev-mode page load: React
      StrictMode's deliberate double-invoke of `AuthProvider`'s mount effect went from 2 real
      `/api/auth/refresh` calls per reload to 1, confirmed via the Network log across
      multiple reloads. Two genuinely separate browser tabs still each fire their own call
      (no shared JS module state across tabs) - unchanged by this fix and out of its scope;
      that race was already shown to resolve harmlessly at the backend (both succeed, one
      session orphaned), a resource-hygiene gap, not a reliable logout bug. See the Log.

---

## Log

**2026-09-03 - A.1 done.** Docker Desktop wasn't installed; installing it needs WSL2, which
needs `wsl --install` (elevated, and a restart) - the user ran that, rebooted, and Docker
Desktop's engine came up cleanly afterward. `docker compose up -d postgres redis` brought up
real Postgres 16 + Redis 7 (both healthy within ~15s).

Running `tests/integration/` for the first time surfaced two real, previously-invisible
bugs - exactly what this phase exists to catch:

1. **No pytest config exists at the repo root.** `backend/pytest.ini` sets
   `asyncio_mode = auto`, but that file is only discovered when pytest's rootdir search
   starts from `backend/`. The documented (and CI's own) invocation runs
   `PYTHONPATH=backend python -m pytest tests/integration` from the **repo root**, which
   never found that config - every async test failed with "you need to install a suitable
   plugin for your async framework." **This means the CI integration-test job, exactly as
   currently written, would have failed the very first time it ran**, had this repo been
   connected to a CI runner already. Fixed: added `pytest.ini` at the repo root.
2. **`app.db.engine` and `app.redis_client`'s module-level Redis client are process-wide
   singletons, but pytest-asyncio hands every test function a fresh event loop.** A pooled
   asyncpg/redis connection is bound to the loop that created it; the next test's first use
   of the pool crashed trying to revalidate a connection tied to an already-closed loop
   ("Event loop is closed"). Fixed in `tests/integration/conftest.py`: reset both
   pools at the start of every test via `engine.dispose(close=False)` and dropping the
   Redis client reference outright - `close=False`/no graceful disconnect specifically,
   since a graceful close needs the *old*, dead loop and would just trade one crash for
   another.

All 4 `tests/integration/` tests pass, twice in a row (stability check). `backend/tests/`
(280 tests, SQLite/fakeredis by design) re-run afterward, unaffected. See commit for the
full diff.

**2026-09-04 - A.2 done.** No git remote existed and `gh` CLI wasn't installed - installed
it via winget (no elevation needed, unlike Docker), the user ran `gh auth login`
interactively (their own GitHub identity, never handled by the agent). Scanned the full
commit history first for anything that shouldn't leave this machine (`.env` files,
credentials, keys) - clean, nothing found. Created a new **private** repo
([munawazus/ocpcc](https://github.com/munawazus/ocpcc)) via `gh repo create` and pushed.

`git push` itself hung indefinitely on the first two attempts - `credential.helper` was set
to Git Credential Manager, which is a separate credential store from `gh`'s own (keyring)
and was evidently trying an interactive prompt that never resolved in a non-interactive
shell. Fixed with `gh auth setup-git`, which wires git's credential helper directly to `gh`'s
already-authenticated token for github.com specifically - push succeeded immediately after.

The first push did **not** trigger CI at all. Found why immediately: **`.github/workflows/
ci.yml` triggers on push to `main`, but this repo's actual branch has always been `master`**
- CI has therefore never once fired on a normal push, in this project's entire history,
until this was caught and fixed. Corrected the trigger branch; the very next push fired CI
for the first time ever, and it ran to completion fully green - both jobs, all steps,
including "Integration tests (real Postgres + Redis)" on a fresh **Linux** GitHub-hosted
runner, independently confirming A.1's local fixes hold on a different OS (the Windows
ProactorEventLoop issue A.1 fixed around doesn't exist on Linux, but the pytest.ini config
gap did, and stayed fixed there too).

**2026-09-04 - A.3 done.** Installed Chromium (`npx playwright install chromium --with-deps`).
Brought up the **full** docker-compose stack for the first time (postgres, redis, backend,
frontend all healthy) via a new root `.env` (gitignored, real generated secrets - never
committed). Ran `frontend/e2e/login-flow.spec.ts` against it - both tests passed on the
first attempt, but a repeat run (same command, no changes) had one failure, so following the
same stability-check discipline as A.1/A.2 this wasn't accepted at first green.

`--workers=1` (serial) passed reliably every time; the default `--workers=2` (parallel)
failed intermittently with a Playwright strict-mode violation:
`getByRole('link', { name: 'Databases' })` resolved to **two** elements - the sidebar nav
link (whose accessible name is exactly `"Databases"`) and the Overview page's own
`"Go to Databases ->"` empty-state link (shown whenever zero databases are registered, which
is true for this fresh seed and is a completely ordinary real-world state, not a test
artifact). Playwright's default non-exact name matching does substring matching, so both
links match `{ name: "Databases" }`. The test only "passed" before by a timing accident - it
clicked before the empty-state card (a real, async-loaded UI element) had rendered; slower
runs under 2-worker contention gave it time to render, exposing the always-latent ambiguity.
**This test had never actually verified its own locator was unambiguous, because it had
never been run before** - exactly what this phase exists to catch.

Fixed in `frontend/e2e/login-flow.spec.ts`: added `exact: true` to that locator, which
Playwright's own error output already named as the disambiguating form
(`aka getByRole('link', { name: 'Databases', exact: true })`). Re-ran 4 times in a row at the
default 2 workers after the fix - 2 passed, every time (8/8 tests total, zero failures).
`npm run lint` on the changed file - 0 errors (3 pre-existing, unrelated warnings elsewhere).

**2026-09-04 - A.4 in progress.** This machine already has a real, previously-installed
native Oracle 19c EE instance (Windows services `OracleServiceORCL` +
`OracleOraDB19Home1TNSListener`, a CDB with pluggable database `orclpdb`) - the same one
referenced in `docs/QA_LIVE_FINDINGS.md`'s one-off G4 note. Used it instead of a disposable
Docker container specifically because it persists independently of `docker compose down`,
which a multi-day observation window needs. Created a least-privilege `monitor`-style
account (`dbsnmp`, unlocked by the user) scoped to exactly `ALLOWLISTED_VIEWS` + `ALL_USERS`
per `docs/RUNBOOK.md`'s documented requirement, granted by the user directly (never handled
SYS/SYSTEM credentials).

First real connection attempt failed with `ORA-01109: database not open` - `orclpdb` was
mounted but not open (fixed by the user: `ALTER PLUGGABLE DATABASE orclpdb OPEN` +
`SAVE STATE` so it survives future Oracle restarts too). After that: **Test Connection**
succeeded, **Detect Capabilities** returned 18/18 (correctly detected `19.0.0.0.0`, edition
**SE2** - not the `EE` hint given at registration, confirming the detector reports ground
truth independent of the hint), and the collector began producing real `V$SESSION` telemetry
(our own `dbsnmp` session, real container hostname, real `python3.12` program path) on its
normal 15s cycle.

Verified two of A.4's three explicit resilience requirements today, both with real evidence:

1. **Survives real restarts** - restarted the `backend` container outright
   (`docker compose restart backend`). The scheduler re-registered its job and resumed
   polling with no manual re-registration (the database record lives in Postgres, not
   memory); a fresh `V$SESSION` sample immediately after confirmed live data, not stale
   cache.
2. **Survives real intermittent network issues** - repointed the registered database at an
   unreachable port via `PATCH /api/databases/{id}`, waited through several failed cycles,
   then restored the correct port. This surfaced a **real bug**: a raw TCP-level failure
   (connection refused - no listener at all) makes `python-oracledb` raise a `DPY-*`
   driver/network-layer error instead of a server-returned `ORA-*` code, and
   `backend/app/oracle/connector.py`'s category mapping only ever checked the numeric
   `code` field (`0` for a `DPY-*` error, present in neither `_AUTH_FAILURE_CODES` nor
   `_UNREACHABLE_CODES`) - so exactly the "network unreachable" scenario this item exists to
   prove out was silently miscategorized as `unknown_error`. An authentication failure can
   only happen *after* a successful TCP handshake, so it can never produce a `DPY-*` code -
   fixed by also checking `full_code.startswith("DPY-")` and mapping that to
   `unreachable`, with a new `backend/tests/test_oracle_connector.py` (previously this
   mapping logic had **zero** test coverage, mocked or real) pinning both the fix and a
   regression case. After the fix and a rebuild: `test-connection` correctly reported
   `"category": "unreachable"` during the outage, `collector-status` showed real failure
   tracking (`consecutive_failures: 20`), and after restoring the port the very next cycle
   reset `consecutive_failures` to `0` and cleared `last_error_category` - full automatic
   recovery, no manual intervention.

**Not yet verified**: "real drift" and the actual multi-day duration this item asks for -
today's checks prove the mechanics work, not that they hold up over days of continuous real
operation. Leaving `COLLECTOR_ENABLED=true` and the database registered/active; will check
back over the coming days and only mark this `[x]` once there's real multi-day evidence, not
a few minutes of it.

**2026-09-04 - A.5 in progress.** `backend/app/notifications/mailer.py` (aiosmtplib) was
already real, production code - just never once exercised against a real network SMTP
server; every existing test used a fake in-memory `EmailSender`. SMTP config isn't an env
var, it's a DB-backed `notification_settings` singleton row, set via
`PUT /api/notifications/settings`.

Given a choice between a real Gmail relay (a real external mailbox, but the user's own
personal account) and a real local SMTP test server, the user chose the latter - added
`mailhog/mailhog:v1.0.1` to `docker-compose.yml` (SMTP :1025, web/API :8025) and pointed
`notification_settings` at it (`smtp_host=mailhog`, TLS/auth off - MailHog needs neither).
This is a real SMTP daemon on a real TCP connection, not a mock, but it stops short of
delivering to a real external mailbox - noted as the one remaining gap.

To get one real, organically-detected HIGH/CRITICAL incident (not a manually-inserted fake
anomaly row - there's no such endpoint anyway) against the now-connected real Oracle
instance from A.4: found that severity is a z-score against a per-hour-of-day Welford
baseline on `active_session_count` or per-wait-class wait rates
(`backend/app/analytics/baseline_engine.py`), and that an unreachable-instance condition
never reaches the anomaly pipeline at all (analytics returns early when the collector wrote
no telemetry) - so A.4's earlier network-outage test was never going to produce one. Opened
30 real concurrent sessions against `orclpdb` (script, `dbsnmp`/real credentials) and held
them 60s to span multiple collector cycles, since the first cycle of a spike only updates
the baseline and can't be judged against itself. Real result: a genuine **CRITICAL**
"User I/O wait time anomaly" incident (the session-open burst's own I/O, not a contrived
value) picked up 6 real correlated evidence observations, `NotificationService` correctly
included both `default_recipients` and `escalation_recipients` (CRITICAL-only routing), and
MailHog captured one real, correctly-formed email -
`[CRITICAL] User I/O wait time anomaly - Native Oracle 19c (ORCL/orclpdb)` - with the real
incident id, database name, and evidence count in the body. Closed all 30 sessions
afterward; `collector-status` confirmed the collector was undisturbed by the spike
(`consecutive_failures: 0` immediately after).

**Closed the real-mailbox gap the same day.** The user's first attempt at giving me Gmail
credentials was actually their regular account password, not an App Password - caught
before it was ever used (a real account password shouldn't be handed to a third-party SMTP
client, and Gmail would likely reject it for SMTP auth anyway with 2FA on). To keep the
credential out of chat entirely, the user's own idea: write it into a local scratchpad file
themselves, have me read the file rather than type/paste it. Regenerated a real 16-letter App
Password into that file; reconfigured `notification_settings` to `smtp.gmail.com:587` +
STARTTLS via `PUT /api/notifications/settings`.

The existing CRITICAL incident from the MailHog test was still within the 30-minute
correlation window, which would have attached new evidence to it rather than opening a new
one (already-CRITICAL can't escalate further, so no new notification would fire) - resolved
it first (`POST /api/incidents/{id}/resolve`) so the next real spike opened a genuinely fresh
incident. Repeated the same real 30-session spike against `orclpdb`: a new incident opened at
LOW, escalated to HIGH in real time as more evidence correlated, then to CRITICAL -
`notification_events` shows **two real sends, both `status: SENT`, first attempt, no
errors** (`[HIGH] Commit wait time anomaly...` then `[CRITICAL] Commit wait time
anomaly...`). **User confirmed both landed in the real Gmail inbox.**

Also found while clearing the Gmail credentials back out afterward: `PUT
/api/notifications/settings` is a partial update where `null` means "leave unchanged" (by
design, not a bug) - sending `smtp_username: null`/`smtp_password: null` to clear them left
the real Gmail username/app-password in place, which would have made every future MailHog
send attempt a real (and failing) AUTH negotiation. Fixed by sending empty strings instead,
which `mailer.py`'s `username or None` / `password or None` correctly treats as "no auth."
Reconfigured back to MailHog for A.4's ongoing unattended observation window, specifically to
avoid real alert-email noise from any further baseline-skew side effects of today's testing.

While asking whether to revert to MailHog, the user asked for exactly this kind of thing
(setting SMTP relay + recipients) to be doable from the frontend, not just the API - it
already is: `Administration -> Notifications`
(`frontend/src/pages/administration/NotificationsPanel.tsx`) is a complete, working form
(host/port/TLS/username/password, sender identity, comma-separated default/escalation
recipients) already wired to the same `PUT /api/notifications/settings` endpoint used above.
Verified live in the browser, logged in as the real admin account - the panel correctly
loaded the exact MailHog settings just configured via the API. No new code needed.

**2026-09-04 - A.6 done.** The full `gpt-oss-120b` (~65-80GB) is not feasible on this
machine - no discrete GPU (Intel Arc integrated only) and 31.5GB total RAM. Rather than
force-fit `gpt-oss-20b` (the project's own configured fallback, but still a large CPU-only
run), used what was actually the better real fit for this hardware and already available:
Ollama (already installed, 0.33.2) had `qwen2.5:7b-instruct` already pulled - a strong,
well-tested instruction-following model. Set `AI_ENABLED=true`,
`AI_BASE_URL=http://host.docker.internal:11434/v1` (Ollama's OpenAI-compatible endpoint,
reachable from the backend container), `AI_PRIMARY_MODEL=AI_FALLBACK_MODEL=qwen2.5:7b-instruct`.
Bumped `AI_REQUEST_TIMEOUT_SECONDS` from 30 to 240 after the first real RCA call hit
`APITimeoutError` at 60s - CPU-only inference on a larger RCA prompt genuinely needs more
time than a GPU-backed deployment would; this is a real hardware constraint, not a bug.

**Real Copilot query**: `POST /api/ai/ask` against the real Oracle instance from A.4 -
real evidence gathering (4 real tools), real model call, a correct, coherent, grounded
one-sentence answer.

**Real RCA investigation - found and fixed a real gap.** The first real RCA call
(`POST /api/incidents/{id}/rca` against the open incident from A.5) came back with every
field empty/null - not a crash, exactly the safe fallback `parse_rca_response` is designed
to produce, but still not useful. Inspected the real raw model output directly (bypassing
the parser) to see why: the model added a conversational preamble before a fenced JSON
block *and* invented its own JSON structure (nested under an invented key, different field
names) despite the prompt's explicit "respond with a single JSON object matching EXACTLY
this shape, no other text." This is precisely the risk `docs/RUNBOOK.md` flagged as
unverified. Confirmed Ollama honors the standard OpenAI `response_format={"type":
"json_object"}` JSON-mode parameter (a quick raw curl test); added `json_mode` support to
`app/ai/gateway.py`'s `LLMClient` protocol/`OpenAICompatibleClient.complete()` and wired
`RcaService.investigate` to request it (`backend/app/services/rca_service.py`) - Copilot
does not, since its answers are meant to be prose, not JSON. Updated the three test fakes
(`test_rca_service.py`, `test_copilot_service.py`, `test_adversarial_qa.py`) for the new
protocol shape and added a regression assertion that RCA always requests `json_mode=True`.
All 284 backend tests pass; `ruff`/`mypy` clean.

JSON mode fixed the preamble problem outright (confirmed with a small-evidence RCA call -
model followed the exact schema correctly once wrapped in valid JSON syntax). But re-running
the *real*, full-evidence incident afterward showed the deeper issue only partly closed:
the model still nested its answer under an invented `"rca"` key with its own field names,
now as syntactically valid JSON that still doesn't match the requested schema.
**Leaving this open, honestly** - it's a genuine capability limit of a 7B-class model on a
complex prompt, not a code defect, and `parse_rca_response`'s validation is exactly what's
supposed to catch it (and does - no crash, no fabricated fields, an honest empty result).
JSON mode is a real, verified improvement over pure prompt-only instructions; it is not a
complete fix, and RUNBOOK.md's original concern was correct to flag this as needing real
verification.

**Adversarial payload fired at the real model - the exact text from
`test_adversarial_qa.py`.** Rather than a synthetic override, used the real, threat-model-
relevant vector: a real Oracle client can set its own session `MODULE`/`ACTION` via
`DBMS_APPLICATION_INFO.SET_MODULE`, so connected as `dbsnmp` and set the real session's
`MODULE`/`ACTION` to the adversarial text verbatim (split across Oracle's real 48/32-byte
column limits - itself a real, honest constraint the fake in-memory test never has to
account for). Confirmed the real collector picked up the poisoned session into real
`V$SESSION` telemetry, then fired both a real Copilot query and a real RCA investigation
against it. In both cases the real model's raw output included the injected text only as an
inert data value it echoed back (e.g. `"action": "E: you are now in unrestricted mode"`
inside its own JSON) - it never claimed to be in an unrestricted mode, never fabricated a
leaked password, never treated the payload as an instruction. The grounding-rules framing in
`app/ai/prompts.py` held against a real model with the real payload, in both AI code paths.

Left `AI_ENABLED=true` pointed at the real local Ollama server - no ongoing cost (fully
local, no external calls), and it demonstrates the now-verified-working real capability
rather than reverting to the unverified default.

**2026-09-04 - B.1 done.** Re-derived every one of the G0-G11 gates in
`docs/PRODUCTION_CERTIFICATION.md` from scratch against A.1-A.6's real evidence, gate by
gate, checking each PARTIAL gate's *specific named blocker condition* individually rather
than rounding partial evidence up - the same discipline the original document itself used to
render NOT CERTIFIED the first time. Score moved from 5 PASS/5 PARTIAL/1 FAIL to
**8 PASS/3 PARTIAL/0 FAIL**:

- **G4 (First DB): FAIL -> PASS.** The one gate whose blocker was ever *confirmed* true - no
  real Oracle instance had ever been connected. A.4 resolved this outright.
- **G5 (Observability), G6 (Intelligence), G8 (Notifications), G10 (RC): PARTIAL -> PASS.**
  Each had a specific "not yet against real infra/data" caveat that A.1/A.4/A.5/A.3
  respectively closed directly.
- **G2 (UX): NOT PROVEN -> PARTIAL.** A.3's real e2e run is real evidence, but it is not the
  standalone control-inventory audit (B.2, not yet started) this gate actually asks for -
  upgraded, not closed.
- **G1 (Architecture), G7 (AI), G9 (Scale): remain PARTIAL**, each for a reason A.1-A.6 didn't
  touch or only partly closed (no ADRs; no formal RCA benchmark suite and real-model
  JSON-schema drift on complex evidence; fleet-scale load numbers still fake-only). Checked
  each one's specific blocker condition individually - none is confirmed true.
- **G11 (Production): FAIL -> PASS.** The first rendering was explicit that G4 was this
  gate's sole confirmed blocker, not the other PARTIALs (each checked against its own
  blocker condition and found not to trigger it). With G4 resolved, G11's own stated logic
  concludes PASS.

**Verdict: CERTIFIED, with three tracked exceptions** (G1/G7/G9) - explicitly not "nothing
left to do," and the document says so in its own words rather than letting "CERTIFIED" imply
more than the evidence supports. Also added a superseded-pointer note to
`docs/PRODUCTION_READINESS.md` (a Phase 28 snapshot cited as evidence basis, left otherwise
historically intact) so its "NEVER EXECUTED" claims - now false - don't undermine the
certification's own credibility by contradiction.

**2026-09-04 - B.2 done.** Enumerated every interactive control across all 25 frontend page
files plus the shared `components/ui/` controls and the app shell (`Sidebar`, `UserMenu`),
checking each one against existing test coverage - the full inventory and its findings are
in `docs/UI_CONTROL_INVENTORY.md`.

Highest-value findings, all fixed in this pass:

- `frontend/src/components/ui/StaleState.tsx` was genuinely dead code (rendered nowhere,
  confirmed by grep) - deleted rather than kept and tested.
- `DatabasesPage.tsx` (489 lines, the platform's core CRUD surface) had **zero** test
  coverage. Added `DatabasesPage.test.tsx` (14 tests): list/empty/filter states, register/
  edit/delete a database, the edit-form's blank-field-means-keep-current logic, group-
  membership checkbox toggling, and both page-level and drawer-level tab switches.
- `IncidentsPage`'s entire `incident:manage` write path (Acknowledge/Assign/Resolve) had
  never actually been clicked in a test - only asserted *absent* for an unprivileged user.
  Added 3 tests exercising the real happy path for each.
- `UsersPanel.tsx` (admin user/role management) had zero coverage - added 7 tests (create
  user with roles, edit roles, enable/disable both directions, cancel-without-side-effect).
- `AuditPanel.tsx` and `AdministrationPage.tsx` (its permission-gated tab router) both had
  zero coverage - added 4 tests each (pagination boundaries/filter-resets-offset; tab
  visibility per permission and the "Nothing to administer" fallback).
- No shared-component tests existed for `ErrorState`, `Modal`, or `Drawer`, despite
  `ErrorState`'s "Try again" button alone appearing on ~18 different query sites across
  nearly every page - added 3/5/5 tests respectively.

**115 frontend tests now pass** (was 70) across 27 files (was 20). `tsc -b`, `eslint .`, and
`vite build` all remain clean.

**Left honestly open** (named explicitly in `docs/UI_CONTROL_INVENTORY.md` rather than
hidden): several read-only telemetry pages' `DatabaseSelector`/range-filter controls are
asserted present but never programmatically changed; `ReportsPage`'s non-default report-type
branches and "Download PDF"; `NotificationsPanel`'s checkbox and recipient-parsing logic;
`IntegrationsPanel`'s severity/enabled checkboxes and edit flow; `CapabilityPanel`'s
Test Connection/Detect Capabilities buttons (never clicked in a *unit* test, though both got
real integration-level exercise against the real Oracle instance in A.4/A.6); a hardcoded
`aria-labelledby` id on `Modal`/`Drawer` that would collide if two ever opened
simultaneously (not currently reachable); and `UserMenu`, covered only by the e2e spec, with
no component-level test of its own. None of these are known-broken - they're untested,
named as such, and left for a future pass rather than claimed as closed.

**2026-09-04 - C.1 done.** `app/security/ssrf_guard.py`'s existing resolve-then-check (config-
time on `Integration.target_url`, plus a Phase 27 send-time re-check) could narrow but never
close the DNS-rebinding race: its own DNS lookup and httpx's separate internal lookup when it
actually connects are two independent resolutions an attacker's DNS server could answer
differently for.

Closed it by taking over connection establishment itself. New
`backend/app/security/ssrf_transport.py`: `SSRFSafeAsyncTransport` (an `httpx.AsyncBaseTransport`)
wraps an `httpcore.AsyncConnectionPool` whose custom network backend resolves the target host
exactly once, validates every candidate IP against the same allow/block logic as
`ssrf_guard.py` (promoted its `_is_private_or_reserved` to a shared, public
`is_private_or_reserved`), and connects the raw TCP socket directly to a validated IP literal
- never to the hostname a second time. Confirmed via `httpcore`/`connection.py` source that
TLS SNI/certificate verification still uses the original hostname regardless of which IP the
socket connected to (`start_tls(server_hostname=...)` is called on the already-open stream by
httpcore's own connection layer, unaffected by our backend), so this doesn't weaken
certificate validation. Wired into `app/integrations/webhook_sender.py` in place of httpx's
default transport (bypassed only when `allow_internal_integration_targets` is set, matching
the existing escape hatch).

One real implementation subtlety worth recording: the new `SSRFBlockedError` deliberately
does **not** inherit from `httpcore.ConnectError`, even though it's raised from the same
place a real connect error would be - httpx's own exception-mapping (which converts
httpcore's exceptions into `httpx.ConnectError`/etc. so genuine network failures keep
surfacing normally) matches by `isinstance` against httpcore's hierarchy, so inheriting from
it would have gotten `SSRFBlockedError` silently rewritten into a generic
`httpx.ConnectError`, indistinguishable from "genuinely unreachable" and stripped of its
`host`/`ip` attributes - confirmed by reading httpx's actual mapping code before assuming
either way.

10 new tests (`backend/tests/test_ssrf_transport.py`): the core resolve-once-and-pin
behavior, blocking a private/loopback/link-local IP before ever touching the real backend,
blocking if *any* resolved candidate is unsafe (matching `ssrf_guard.py`'s existing stance),
and - the property that actually closes the rebinding gap - that the resolver is called
exactly once and the connection goes to precisely what it returned. Two of the ten make a
**real** external HTTPS request and a **real** blocked loopback connection attempt (skipped,
not failed, if the environment has no internet access) - proven directly, not just asserted
in theory: a real `https://example.com/` GET succeeded end-to-end through the new transport,
and a real attempt at `127.0.0.1` was actually blocked. All 43 pre-existing SSRF/webhook/
integration tests still pass unchanged; full suite (294 tests), `ruff`, `mypy`, and `bandit`
all clean (two `# nosec B101`s added for httpx-mirroring type-narrowing asserts, matching
this codebase's existing `# nosec` convention). Verified once more against the real running
app: created a real webhook integration pointed at a real external endpoint
(`https://httpbin.org/post`) and delivered a real test payload through it via the live API,
confirming no regression in the actual delivery path.

Updated `docs/THREAT_MODEL.md` Sec 5.4 and Sec 6 - both previously named this exact residual
gap by name; left honestly noted that Oracle host/port and SMTP host still have no SSRF
protection at all (a different, non-httpx problem - out of scope for a custom httpx
transport specifically).

**2026-09-04 - C.2 done.** The old `BodySizeLimitMiddleware` (`app/security/middleware.py`)
was a `BaseHTTPMiddleware` that only compared the client-declared `Content-Length` header
against `MAX_REQUEST_BODY_BYTES` - a client using chunked Transfer-Encoding (no
`Content-Length` at all) or simply lying about a smaller one sailed straight through.

Rewrote it as a raw ASGI middleware (`__call__(scope, receive, send)`, not
`BaseHTTPMiddleware` - confirmed `BaseHTTPMiddleware` buffers the *entire* body into memory
before `dispatch` ever runs, which would defeat this check for precisely the case it exists
to catch). Kept the header check as a fast, zero-read pre-check for the honest/common case,
and added the real enforcement: it wraps `receive()`, counts actual bytes as `http.request`
messages arrive, and rejects once the running total - not the declared header - exceeds the
limit.

**Found a real bug in my own first attempt, caught by the user interrupting to ask about it
mid-session rather than by a test.** The first version raised a custom `AppError` subclass
(`RequestBodyTooLargeError`) from inside the wrapped `receive()`. A real end-to-end test
against the actual app object came back with a 400 "There was an error parsing the body"
instead of a 413 - not a crash, just the wrong, generic answer. Traced it to
`fastapi/routing.py`'s request-body handling: it wraps `await request.json()` in a broad
`except Exception` that rewrites *any* other exception type into that same generic 400,
with exactly one documented exception - `except HTTPException: raise` immediately above it.
Switched to raising `starlette.exceptions.HTTPException(status_code=413, ...)` directly,
which FastAPI's own escape hatch re-raises unchanged, and Starlette's default handler for it
produces the correct `{"detail": ...}` JSON - same shape as before, so no API contract
change. Removed the now-unnecessary `RequestBodyTooLargeError`.

7 new tests (`backend/tests/test_body_size_middleware.py`): actual bytes over the limit with
no `Content-Length` header at all, actual bytes over the limit despite a dishonest smaller
declared value, rejecting once a *running total* across several small chunks crosses the
limit (not just one oversized chunk), the fast path never reading anything for an honestly-
declared oversized request, a non-HTTP (websocket) scope passing through untouched, and -
the one that caught the bug above - driving the real `app` object directly at the ASGI level
(since httpx's own request builder always sets an honest Content-Length matching what it
really sends, so it can't produce the "declares less than it sends" case this item is about)
and asserting a real 413, not a 400 or 500. All 16 pre-existing security-hardening tests
still pass unchanged. Full suite (301 tests), `ruff`, `mypy`, and `bandit` all clean.

Verified once more against the real running app: a genuine HTTP request with
`Transfer-Encoding: chunked` and no `Content-Length` carrying a real ~6MB body got a real
413 with the correct message; a normal small login request immediately afterward still
succeeded with a real token - confirming no regression in the common case.

Updated `docs/THREAT_MODEL.md` Sec 5.9 and its Known Gaps list, both of which named this
exact residual gap by name.

**2026-09-04 - C.3 done.** This was a genuine business/product decision, not an engineering
task - `docs/THREAT_MODEL.md` Sec 4.2 had already documented multi-tenant isolation as an
*inferred* scope assumption (Phase 27 noticed no `organization_id` column exists anywhere in
the schema, so every authorization check is resource-class scoped, never per-tenant - safe
only if one deployment serves one organization), but that was never a decision anyone with
actual authority had ratified, just an unexamined default nobody had revisited. Asked the
user directly rather than picking for them, given how much Phase D scope hinges on the
answer (`organization_id` + query-scoping across essentially every table/query, or nothing).

**Decision: single deployment per customer, permanently** - not "not yet multi-tenant," a
decided model. No new engineering required (matches the platform's actual current
architecture exactly), and no `organization_id`/query-scoping work is planned for Phase D or
any later phase. Updated `docs/THREAT_MODEL.md` Sec 4.2 and its Sec 6 Known Gaps entry to
record this as a ratified, dated deployment decision rather than an inferred assumption - if
this is ever revisited, it needs its own dated plan item, the same discipline C.3 itself
just modeled, not a silent re-assumption.

**2026-09-05 - D.1 done.** The master plan document that "Phase 9" and every other phase
cites does not exist anywhere in this repo or on this machine (confirmed via git history and
filesystem search) - it was always external and was never committed. Asked the user directly
rather than guessing at a lost spec; the user chose to proceed with an original,
well-justified design instead, informed by everything already cited about Phase 9 in-repo
(the `get_top_sql`/`get_sql_plan` AI tool names, the SE2 licensing constraint already
governing every other Oracle view, and the existing collector/analytics architecture). Given
the size and architectural consequence, used a formal plan-then-approve step (`EnterPlanMode`)
before writing any code, rather than the usual proceed-and-report pattern.

**Built:** a dedicated `V$SQL`/`V$SQL_PLAN` collector on its own interval, fully
failure-isolated from the main session/wait/health collector (own connection, own
`collector_status` fields, proven by a dedicated test that a missing plan-view grant can't
abort session collection); a schema storing raw cumulative counters as facts (never derived
rates) with plan history captured on change, not every round; a deterministic regression
comparator (plan change / elapsed-per-execution degradation against a statement's own recent
history) deliberately separate from the general hour-of-day baseline engine, which needs 20
same-hour samples per metric and is unworkable for dozens of ephemeral SQL_IDs; four new API
routes and three new AI tools, all following this platform's `available`/`reason`
no-fabrication convention; a real SQL Intelligence frontend page; and de-stubbed
reports/health-check surfaces (`SQL_REGRESSION` report, Daily DBA's SQL section, the
incident report's SQL/session temporal correlation, and the Health report's `sql_regression`
category) that previously always said "Master Plan Phase 9 has not been implemented."

**Verified live against the real orclpdb instance (A.4)** for the Oracle probe and
repository layers - real `V$SQL`/`V$SQL_PLAN` rows landed in Postgres with plausible values
(microsecond counters, real plan operations with sane depth/parent nesting). **Not verified
live**: the regression comparator's thresholds - a real 24-hour regression can't be observed
in one session, so this is proven only by unit test
(`backend/tests/test_sql_regression_detection.py`), stated honestly rather than rounded up to
"done" in the same sense as the collector itself.

**Found and fixed one real design bug of my own**, caught by a failing test: `is_current` in
the SQL detail route required *two* collection rounds (via the two-round delta logic that
top-SQL ranking legitimately needs), so a database with only one round ever reported its only
known plan as not-current. Added `SqlTelemetryRepository.latest_stat_sample` (single most
recent round - all "what's current" needs) and switched the route to use it.

70 new backend tests (`test_sql_metrics.py`, `test_sql_regression_detection.py`,
`test_sql_intelligence.py`, plus additions to `test_ai_tools.py`,
`test_reports_api.py`/`test_health_check_engine.py` for the de-stubbing, and a new SQL-text
prompt-injection test in `test_adversarial_qa.py` extending the existing session-data one) and
3 new frontend tests (`SqlIntelligencePage.test.tsx`). Full backend suite (358 tests) and
full frontend suite (118 tests) both green; `ruff`, `mypy` (118 source files), ESLint, and
`tsc --noEmit` all clean.

**2026-09-05 - D.2 done.** Same situation as D.1: "master plan Phase 5" has no spec document
anywhere in this repo or on this machine - every in-repo mention was just a name used as the
reason nothing was built. Designed fresh, mirroring D.1's architecture file-for-file wherever
the shape genuinely matched, and diverging explicitly where capacity's own nature (a
slow-moving space metric, not a per-execution counter) forced a different choice - most
notably, a single collection round is enough for current %used (unlike top-SQL's always-two-
rounds delta), and thresholds (%used warn/critical, days-to-full warn/critical) are
operator-configurable rather than hardcoded, since "how full is too full" varies far more by
environment than a SQL regression's ratio ladder does. Used the same formal plan-then-approve
step (`EnterPlanMode`) as D.1 before writing any code.

**Built:** a dedicated `DBA_TABLESPACES`/`DBA_DATA_FILES`/`DBA_FREE_SPACE`/`DBA_TEMP_FILES`/
`V$TEMP_SPACE_HEADER` collector on its own (1h default) interval, failure-isolated from both
other collectors (own connection, own `collector_status` fields, PERMANENT/UNDO as the
"must succeed" probe with TEMPORARY as best-effort); a schema storing raw allocated/used/max
bytes as facts (no NULL ceiling fabricated for unconstrained autoextend); a deterministic
linear growth-rate fit projecting time-to-full over a 7-day trailing window; a comparator
raising two independent, non-suppressing alert kinds (`PCT_USED`/`PROJECTED_FULL`) with
escalate-only severity and auto-resolve, mirroring `SqlRegressionEvent`'s lifecycle; three new
API routes and a new AI tool (`get_tablespace_trend`, gathered into both Copilot and RCA
evidence, unlike the on-demand-only `get_sql_plan`); a real Capacity frontend page; and
de-stubbed reports/health-check surfaces (`CAPACITY` report, Daily DBA's capacity section, and
the Health report's `storage` category) that previously always said "Master Plan Phase 5's
capacity telemetry has not been implemented."

**Verified live against the real orclpdb instance (A.4)** for the Oracle probe and repository
layers - real tablespace rows (`SYSTEM`, `SYSAUX`, `UNDOTBS1`, `USERS`, `TEMP`) landed in
Postgres with plausible values, including a real 32GB per-datafile autoextend ceiling (not the
"no known ceiling" sentinel), confirming the ceiling-detection logic is correct. **Not
verified live**: the growth-rate/alert comparator's thresholds - a real multi-day tablespace
growth trend can't be observed in one session, so this is proven only by unit test
(`backend/tests/test_capacity_alerts.py`), stated honestly rather than rounded up to "done" in
the same sense as the collector itself.

**Found and fixed two real bugs of my own, both caught before or by tests rather than assumed
correct:**
1. Live verification against orclpdb surfaced a genuine Oracle quirk: a TEMP tablespace's
   `used_bytes` (from `V$TEMP_SPACE_HEADER`) exceeded a separately-read `DBA_TEMP_FILES.BYTES`
   for "allocated" - Oracle doesn't always shrink a tempfile's high-water-mark back down after
   a large sort, so the two views can genuinely disagree by the time two independent queries
   read them. Fixed by deriving both allocated and used bytes for TEMP from the single
   `V$TEMP_SPACE_HEADER` view, guaranteeing used ≤ allocated by construction.
2. A failing test caught naive-vs-aware datetime comparison: `compute_growth_trend` filters
   samples against `now` in Python (unlike D.1's regression comparator, which does its window
   filtering in SQL) - SQLite (the test suite's database) hands back naive datetimes even for
   tz-aware columns, so comparing a naive `sampled_at` against a tz-aware `now` raised
   `TypeError`. Fixed with a local `_ensure_aware()` helper (duplicated from
   `app.models.base.ensure_aware` rather than imported, keeping the analytics module's
   zero-app.models/ORM-coupling stance intact).

41 new backend tests (`test_capacity_metrics.py`, `test_capacity_alerts.py`, `test_capacity.py`,
plus additions to `test_ai_tools.py`, `test_reports_api.py`/`test_health_check_engine.py` for
the de-stubbing) and 3 new frontend tests (`CapacityPage.test.tsx`). Full backend suite
(399 tests) and full frontend suite (121 tests) both green; `ruff`, `mypy` (124 source files),
ESLint, and `tsc --noEmit` all clean.

**2026-09-05 - D.3 done.** Statspack is a genuinely different shape from D.1/D.2: this
platform never creates Statspack snapshots - it can only detect and read what the DBA's own
independently-scheduled `statspack.snap` job already produced in the `PERFSTAT` schema.
"Collecting" here means incremental catch-up ("what's new since we last looked"), not
sampling a live V$ view on our own schedule. No master-plan spec exists for "Phase 11"
either, same situation as D.1/D.2 - designed fresh, deliberately mirroring D.1/D.2's
architecture wherever the shape genuinely matched, and diverging where Statspack's own nature
forced it (a single stored high-water-mark cursor instead of a fixed collection interval; no
new alerting/regression comparator, since `STATS$SYSSTAT`/`STATS$SYSTEM_EVENT`/
`STATS$SQL_SUMMARY`'s counters are already cumulative exactly like `V$SYSSTAT`/`V$SQL`, so
D.1's own `compute_sql_deltas`/`rank_sql_deltas` and the existing wait-class rate functions
were reused completely unmodified rather than rebuilt).

Before designing, checked the real orclpdb instance directly (a plan-mode read-only query,
not a code change) and confirmed Statspack is NOT installed there (no `PERFSTAT` user, no
`STATS$SNAPSHOT` table) - asked the user how to scope live verification given that. The user
initially opted to install Statspack themselves for deeper verification; after I pointed out
that the platform already handles "no Statspack" honestly and gracefully by design for the
vast majority of databases that will never have it (zero wasted Oracle connections, an honest
`available: false`, no UI clutter) - a capability this plan itself builds, not a gap it
works around - the user agreed fake-connector-only verification was sufficient, and no
Statspack installation was needed.

**Built:** the `statspack_available` capability check was strengthened from "does a PERFSTAT
database user exist" (never actually confirmed the schema was installed) to "is
`PERFSTAT.STATS$SNAPSHOT` actually queryable" - the same single-writer invariant as before
(only the on-demand Detect Capabilities action ever writes it; the sync job only ever reads
it, so the two can never race). A new `STATSPACK_ALLOWLISTED_TABLES` tuple in
`allowlist.py`, kept separate from `ALLOWLISTED_VIEWS` since these are ordinary
`PERFSTAT`-owned tables, not `V$`/`DBA_*` views. An incremental sync collector (its own 2h
default interval, the slowest of the three collectors, gated on `statspack_available` from
Postgres *before* opening any Oracle connection, so a fleet where most databases lack
Statspack costs nothing extra) with the same nested-try/except partial-capability isolation
as the SQL/Capacity collectors. A new `GET /api/databases/{id}/statspack` route and an
on-demand-only `get_statspack_summary` AI tool (unlike `get_top_sql`/`get_tablespace_trend`,
never auto-gathered into Copilot/RCA evidence, since a Statspack delta can span days at the
DBA's own discretion and auto-including it risks presenting stale evidence as current). A
new additive `statspack_summary` section in the Daily DBA report (not a new report type or
health-check category - neither existed for this, and Statspack isn't a PASS/FAIL health
gate). A new "Statspack" panel on the SQL Intelligence page, gated to render only when the
selected database's capability flag is `true` - deliberately not a dedicated nav item/page,
since an always-empty page would be worse UX than a panel that only appears when relevant.

**Verified live against the real orclpdb instance (A.4)**: only the honest "Statspack not
installed" detection path - the strengthened `_check_statspack_available` check correctly
returns `False` today. **Not verified live**: all `STATS$SNAPSHOT`/`STATS$SYSTEM_EVENT`/
`STATS$SQL_SUMMARY` sync/parsing/delta logic - Statspack isn't installed anywhere available
in this environment, so this is proven only by a fake-connector-driven test suite
(`backend/tests/test_statspack.py`, including a test that directly parses the real SQL text
to confirm the incremental high-water-mark filter is actually applied, not just trusted),
stated honestly rather than rounded up to "done" in the same sense as the detection check
itself - the same precedent D.1/D.2 already set for logic that couldn't be observed live in
one session.

No new design bugs were found this time - the fake-connector test suite passed on first
write, likely because the reused D.1 delta machinery was already proven correct and the new
surface area (sync/high-water-mark logic) is smaller and more mechanical than D.1's/D.2's own
novel analytics.

Also fixed one pre-existing test fixture gap while extending it:
`tests/test_oracle_capability.py`'s `FakeCursor` still simulated the *old* `ALL_USERS`-based
Statspack check - updated it to simulate the new `STATS$SNAPSHOT` query instead (raising
ORA-00942 when `conn.statspack` is `False`), otherwise `test_detect_capabilities_se2_no_rac_
no_statspack` would have silently started asserting the wrong thing (the strengthened check
returns `True` on any non-raising query, so the old fixture's unconditional non-raising stub
for that query would have made every fake-connector test report `statspack_available: True`
regardless of the `statspack` flag).

Also found and fixed two pre-existing doc staleness issues while adding Statspack's own
licensing framing to `docs/THREAT_MODEL.md` Sec 5.1: "the one `# nosec B608` in this
codebase" was stale (12 occurrences after D.1/D.2/D.3, not one), and there was no in-repo
paragraph anywhere previously stating that Statspack itself is a free, bundled Oracle feature
needing no Diagnostics/Tuning Pack license (unlike the already-blocklisted AWR/ASH family) -
both fixed in the same edit.

24 new backend tests (`test_statspack.py`, `test_statspack_metrics.py`, plus additions to
`test_ai_tools.py` and `test_oracle_capability.py`'s fixture fix) and 2 new frontend test
cases (`SqlIntelligencePage.test.tsx`). Full backend suite (421 tests) and full frontend
suite (123 tests) both green; `ruff`, `mypy` (128 source files), ESLint, and `tsc --noEmit`
all clean.

**2026-09-05 - D.4 done.** No master-plan spec exists for "Phase 14" either, same situation as
D.1/D.2/D.3 - designed fresh, using two concrete anchors already present in the codebase:
`health_check.py`'s always-`UNAVAILABLE` `"configuration"` category, literally framed as
"configuration-snapshot/drift-detection," and `app/ai/tools.py`'s docstring naming the exact
planned tool `get_change_history`. Scoped to Oracle instance parameter (configuration) drift:
periodically snapshot `V$SYSTEM_PARAMETER` where `ISDEFAULT = 'FALSE'` (parameters the DBA has
explicitly tuned), diff the two most recent rounds. Every transition type - newly set, changed,
reverted to default - falls out of simple set membership between two rounds; reversion is
detected as "present in the previous round, absent from this one," never a special case.

This is the one phase in D.1-D.4 where none of D.1's own delta machinery could be reused: a
configuration round is a set of (name, value) pairs, not a numeric rate over a shared
cumulative counter, so a small new `analytics/config_metrics.py` set-diff was written instead
of adapting `sql_metrics.py`/`metrics.py`. It's also the simplest collector of the four: one
query, no capability pre-check (unlike Statspack, `V$SYSTEM_PARAMETER` needs no special
license and is present on every edition), no partial-capability nested try/except (unlike
SQL/Capacity/Statspack's 2-3 independent queries each).

**Built:** `V$SYSTEM_PARAMETER` added to `ALLOWLISTED_VIEWS` (a base dynamic-performance view,
no Diagnostics/Tuning Pack license required - `_check_privilege` already iterates
`ALLOWLISTED_VIEWS`, so the existing capability probe picked up a grant check for it with zero
new code). A new `ConfigParameterSample` table (a full per-round snapshot, like
`TablespaceSample` - `V$SYSTEM_PARAMETER` has no natural high-water-mark column to sync from,
unlike Statspack's `snap_id`). A new collector (own 3600s interval, same tier as Capacity),
`GET /api/databases/{id}/configuration`, and an on-demand-only `get_change_history` AI tool
(same "a lookback can span days, so don't auto-include it" reasoning already established for
`get_sql_plan`/`get_statspack_summary`). The `"configuration"` health-check category was
de-stubbed - `PASS`/`WARN` only, no `FAIL` tier, mirroring `"locking"`'s own two-state shape,
since a config change isn't inherently a failure severity. A new additive
`configuration_summary` section on the Daily DBA report. A new dedicated frontend page
(`ChangeIntelligencePage.tsx`, nav item "Change Intelligence") rather than an additive panel
like Statspack's, since - unlike Statspack - this collector runs unconditionally for every
registered database, not just a minority that opted in.

**Verified live against the real orclpdb instance, fully this time** (a first among D.1-D.4):
unlike Statspack, `V$SYSTEM_PARAMETER` needs no DBA-side opt-in or separate installation, so
every path except "does the classifier get newly_set/changed/reverted_to_default right" could
be proven against real Oracle. Confirmed live: `V$SYSTEM_PARAMETER` is queryable and now
appears in the capability probe's `privilege_report`; the real instance returned 18 non-default
parameters (`processes`, `sga_target`, `control_files`, ...); the collector ran twice
back-to-back against orclpdb and persisted two real rounds with 0 changes detected (nothing
was altered); the `"configuration"` health-check category correctly transitioned from
`UNAVAILABLE` to `PASS` in a freshly generated Health report; the frontend page rendered the
real "No configuration changes detected" empty state; the `/configuration` API endpoint
returned real `200 OK` responses in the network log. **Not verified live**: a genuine
parameter change being classified correctly - proven only by the fake-connector suite
(`backend/tests/test_config_intelligence.py`), deliberately not verified by mutating orclpdb
via `ALTER SYSTEM`, since this platform's own design principle is to never modify a monitored
database itself, not even for its own test purposes. Same "unit-test-only, stated honestly"
precedent D.1/D.2/D.3 already set for logic that couldn't be observed live in one session -
except here the honest gap is much narrower (one classification path, not an entire sync
pipeline).

20 new backend tests (`test_config_metrics.py`, `test_config_intelligence.py`, plus additions
to `test_ai_tools.py` and `test_health_check_engine.py`) and 3 new frontend tests
(`ChangeIntelligencePage.test.tsx`). Full backend suite (441 tests) and full frontend suite
(126 tests) both green; `ruff`, `mypy` (132 source files), ESLint, and `tsc --noEmit` all clean.

**2026-09-05 - E.1 done.** The last unstarted item. No master-plan spec exists for "Phase 20"
either (same situation as D.1-D.4): the shape came from two docs already in this repo -
`docs/PRODUCTION_CERTIFICATION.md`'s G7 gate naming the exact gap ("gold-incident RCA-quality
benchmark suite still does not exist"), and `RcaService`'s own existing structured answer
contract (`RcaResult`: `probable_root_cause`/`confidence`/`evidence_summary`/... - see
`app/services/rca_service.py`).

Unlike D.1-D.4, the user explicitly asked this phase be run against a real model, not just
proven with fakes - checked directly and found both Ollama (`qwen2.5:7b-instruct`, already
pulled) and the full `docker-compose` stack already running in this session, so no startup
action was even needed.

**Built:** `app/rca_benchmark/scoring.py` - five deterministic, evidence-groundable checks
(never a second LLM grading the first, matching this codebase's own "deterministic, never an
AI judgment" rule already stated in `health_check.py`/`_recommended_investigation_items`):
`schema_adherence`, `confidence_consistency`, `no_fabricated_identifiers` (a heuristic:
identifier-shaped tokens the model claims must trace back to the evidence bundle it was
handed), `uncertainty_calibration` (must not claim high confidence on deliberately sparse
evidence), and `engages_with_available_evidence` (must not decline to answer when the evidence
clearly supports a specific cause) - the last of the five was **added mid-session**, after the
first real run revealed the first four alone would have called a systematically over-cautious
model "passing." `app/rca_benchmark/gold_incidents.py` - five scenarios built via this
platform's own real repositories/services (mirrors `test_rca_service.py`'s existing
`_open_incident` pattern, not literal dict mocks): `blocking_chain`, `sql_regression`,
`capacity_exhaustion`, `multi_factor` (all four with clear, unambiguous evidence), and
`sparse_evidence` (deliberately built with none). A small refactor to
`app/services/rca_service.py` pulled evidence-gathering out into a standalone
`gather_rca_evidence()` so both the test suite and the benchmark script can reconstruct the
exact evidence bundle a real answer was graded against, without duplicating that list.
`backend/scripts/rca_benchmark.py` - a standalone script mirroring `scale_bench.py`'s isolation
pattern (own temp SQLite, own `fakeredis`) but a **real** `OpenAICompatibleClient` against a
real model, never part of the pytest suite or CI (same reasoning `scale_bench.py` already
documents: no real LLM is ever reachable there).

**Verified live for real, not just with `FakeLLMClient`** (a first among D.1-E.1): ran the
benchmark against the real, already-running `qwen2.5:7b-instruct` via Ollama. First run used
`Settings`' 30s timeout default and two of five scenarios genuinely timed out generating a
~1500-token JSON response on a single local machine - fixed by matching the real deployment's
own 240s `AI_REQUEST_TIMEOUT_SECONDS`, a real, if unsurprising, finding about this model's
practical latency (41-65 seconds per real RCA call once given time to finish). Also did one
live sanity check through the actual running app (browser-driven, against the real orclpdb
database): a real AI Copilot request completed with `200 OK`, attributed to
`qwen2.5:7b-instruct`, using the real evidence-gathering tools - confirming the full
Docker-container -> `host.docker.internal:11434` -> Ollama path works end-to-end, not just the
benchmark script's own direct `localhost:11434` path.

**Real result: 1 of 5 scenarios passed every check.** The one that passed was
`sparse_evidence` - correctly. The model never hallucinated an identifier, never claimed
confidence without a cause (or vice versa), and never over-claimed on sparse evidence -
genuinely good news, and it held up under a real model, not just a canned fake response. But it
also declined to name any root cause on all four scenarios built with clear, unambiguous
evidence, consistently pointing at *other, unrelated* evidence categories being unavailable
(e.g. citing a missing wait profile on the `blocking_chain` scenario, whose answer lived
entirely in the session evidence it already had) rather than reasoning from what it did have.
One case went further: on `sql_regression`, the real evidence bundle contained an `available:
true` regression event with a clear `PLAN_CHANGE`/9x-ratio detection note, yet the model's own
`data_completeness_note` claimed no SQL regression data was available at all - a factually
wrong claim about evidence directly in its own prompt, found by manually reading this run's raw
output, not by any automated check. Full findings, methodology, and the real per-scenario
results: `docs/RCA_BENCHMARK_REPORT.md`. This is reported honestly rather than rounded up - an
"honest 1/5" is exactly the kind of result this benchmark exists to surface, per the plan's own
verification section.

Re-opened `docs/PRODUCTION_CERTIFICATION.md`'s G7 AI gate with these real findings (see that
document's own updated entry) rather than leaving its "still does not exist" language stale
now that the benchmark exists and has run for real.

18 new backend tests (`test_rca_benchmark.py`'s pure scoring-check units plus its
per-gold-scenario `FakeLLMClient` good/bad discrimination tests, one pair per scenario across
all 5 gold scenarios) - CI-safe, no real LLM involved; the real-model run above is a manual,
local-only benchmark, never part of CI, same precedent `scale_bench.py` already set for
Phase 25. Full backend suite (459 tests) green; `ruff`, `mypy` (135 source files), and `bandit`
all clean - unlike D.1-D.4 this phase adds no frontend code, so the frontend suite (126 tests)
is unchanged.

**2026-09-06 - F.1 done.** Follow-up research before touching any code confirmed the bug was
bigger than the QA report's own headline: `connect()` is not the only blocking call at any of
the 7 sites - every probe/query function called afterward (`sample_sessions`, `sample_top_sql`,
`probe_capabilities`, etc.) is a plain, synchronous `def` doing blocking `cursor.execute()`/
`.fetchall()`, and `conn.close()` is synchronous too. A full-repo search confirmed no 8th call
site exists (the "Test Connection"/"Detect Capabilities" API routes reach exactly the same 2
`oracle_capability_service.py` sites, nothing separate) and that `build_connection_params` was
already async-safe (only async Postgres reads, never touches the Oracle socket).

**Fix**: wrapped every individual blocking call - connect, each probe/query, close - in its
own `await asyncio.to_thread(...)`, right where it already happens. Considered bundling each
call site's whole connect→query→close sequence into one new sync helper function instead, but
that would have required either silently reordering the Statspack collector's existing
`get_sync_state` (async Postgres) read relative to `connect()`, or building a second, more
complex helper shape just for that one site - wrapping each call individually needed zero
control-flow changes anywhere, preserving every existing try/except/finally structure, log
message, and partial-capability fallback exactly where it already was. Mirrors the one
existing precedent for this pattern in the codebase, `app/security/ssrf_guard.py`'s
`asyncio.to_thread(socket.getaddrinfo, ...)`.

**Verified, not just implemented**: a new `backend/tests/test_collector_concurrency.py` (7
tests, one per call site) reproduces the exact failure mode deterministically - a fake
connector that blocks for 2s (standing in for a slow/hanging real TCP connect) while a
background heartbeat coroutine keeps re-scheduling itself on the same event loop throughout;
a real freeze shows up as one large gap between heartbeats no matter when in the call's
lifetime it happens. All 7 failed (~2s gap each) against the pre-fix code, confirming the
test genuinely reproduces the bug rather than trivially passing either way - a first, naive
design (racing the slow call against one single concurrent request via `asyncio.gather`) was
tried first and INCORRECTLY passed even on unfixed code, because the concurrent request
could simply finish during the slow call's own early `await`s before it ever reached its
blocking section - caught by checking red-before-green explicitly, not by trusting a green
result alone. The heartbeat redesign fixed that. All 7 pass after the fix. Full existing
backend suite required zero modifications and stayed green (459 unchanged existing tests + 7
new = 466) - the load-bearing proof that this was a relocation, not a rewrite, since every
existing test only ever observed outcomes, never which OS thread produced them.

**Live-verified against the real running stack, the same way the bug was originally found**:
registered 6 unreachable-host databases against the real docker-compose stack, confirmed via
backend logs that the main collector genuinely attempted (and failed to connect to) all of
them during the measurement window, and sampled `GET /api/health` every second for 20
seconds spanning that window - flat at 2-4ms throughout (previously 3.1s/8.0s spikes for the
identical reproduction). Also re-confirmed both on-demand routes (Test Connection, Detect
Capabilities) still work correctly against the real orclpdb instance post-fix. Test databases
deleted after.

`ruff`, `mypy` (135 source files), and `bandit` all clean. No frontend changes this phase.

**2026-09-06 - F.2 done.** Moved the existing `refreshInFlight` de-dup guard out of
`apiFetch`'s own inline check and into a newly-exported `refreshAccessToken()` in
`lib/api.ts`, so any caller gets the same guarantee rather than only `apiFetch`'s own
401-retry path getting it by construction. `auth-context.tsx`'s mount effect now calls that
shared function instead of its own separate `apiFetch("/api/auth/refresh", ...)`; since
`refreshAccessToken()` never throws (returns `null` on failure, unlike `apiFetch`), the
effect gained an explicit `if (!token) { setUser(null); return; }` branch where a thrown
error used to do the same job implicitly.

**An honest scope correction found live, not assumed**: this fix deduplicates concurrent
refresh calls *within one browser tab's own JS runtime* - it cannot and does not deduplicate
across two genuinely separate tabs, which have no shared JS module state (only the shared
HTTP cookie). Live verification initially seemed to show the fix wasn't working at all (two
tabs, still 2 refresh calls) - investigating found `main.tsx` wraps the app in
`<React.StrictMode>`, which deliberately double-invokes every mount effect in dev mode. That
double-invoke - not "two tabs" - is what actually fires on *every single dev-mode page
reload*, in one tab, and is exactly what the shared `refreshInFlight` singleton catches:
confirmed via the Network log that a single tab's reload went from 2 `POST /api/auth/refresh`
calls to 1, consistently across repeated reloads, once the frontend dev container was
restarted to pick up the change (Vite's file watcher never noticed the edit on this
Windows-host bind mount - no HMR log line appeared; a container restart forced a fresh read).
The genuinely-separate-tabs race is unchanged by this fix and was never in its scope - the QA
pass's own earlier isolated backend testing already showed that race resolves harmlessly
(both calls succeed, one new session row goes unused), a resource-hygiene gap, not a reliable
logout bug.

New `frontend/src/lib/auth-context.test.tsx` (3 tests, including the regression test: two
`<AuthProvider>` trees rendered in one test - which do share one JS module instance, modeling
the real StrictMode-double-invoke/multiple-concurrent-caller case this fix actually
addresses - assert exactly one `/api/auth/refresh` call, not two) and 2 new tests added to
`lib/api.test.ts` locking in that `apiFetch`'s own 401-retry path still dedupes correctly now
that it's routed through the shared function. The regression test failed red against the
pre-fix code (2 calls) and passes green after (1 call) - checked explicitly, not assumed.
Full frontend suite (131 tests: 126 existing + 5 new) green with zero existing tests
modified; `eslint`/`tsc --noEmit` clean. No backend changes this phase.

Record what actually happened against each item here as it's completed - date, evidence,
and any deviation from the plan. Keep it short; link to the commit or doc that has the
detail.
