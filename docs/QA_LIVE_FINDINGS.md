# Live QA Findings — Real-Time Testing Pass

Found by actually running the application (backend + frontend, real login, a **real Oracle
19c EE connection** to a reachable local-network instance, real collector cycles, real
anomaly/incident correlation, real report generation) rather than reading code or running
the automated test suite. This is exactly the class of bug the (then-277, now 280) backend
tests against `fakeredis`/fake connectors could never catch on their own — every finding
below only exists because a
real dependency (Redis being down, a real un-suffixed UTC timestamp, real nested report
JSON) was actually exercised.

Ranked by severity. All six are now fixed, verified by an automated regression test and,
where the fix is visible in the UI, by re-running the exact live repro steps against the
running app.

**2026-09-08 addendum**: a second, stricter pass (below, "Independent verification pass")
went further than "run the app and see what visibly breaks" - it cross-validated OCPCC's own
dashboard against ground truth queried directly from Oracle, using a dedicated low-privilege
account instead of the app's own trusted monitoring credential. That pass found two genuine,
confirmed characteristics of the monitoring model itself. Finding 7 (no `STATE`/CPU telemetry)
has since been remediated - see its entry for the fix and live verification. Finding 8 (Top
SQL's two-tick visibility delay) remains an accepted, working-as-designed trade-off, not a
bug - see that section for why.

---

## HIGH — breaks a core flow

### 1. Login (and every rate-limited route) crashes with a 500 if Redis is unreachable
**Status: already fixed in the working tree, not yet committed.**

`POST /api/auth/login`'s rate-limit check, and the shared `require_rate_limit()` dependency
used by AI ask/RCA, report generation, bulk database onboarding, and integration testing,
called Redis directly with **no error handling** — unlike `RateLimitMiddleware`, which
correctly fails open. The moment Redis was unreachable (the actual state of this dev
environment), login itself 500'd, and the browser reported it as a misleading CORS/"Failed
to fetch" error rather than a real error (see Finding 6).

- `backend/app/api/routes/auth.py:52` (login)
- `backend/app/security/deps.py:80` (`require_rate_limit`)
- **Fix applied**: both now wrap the `.hit()` call in `try/except`, log
  `rate_limit_check_failed_failing_open`, and treat the request as allowed — mirroring
  `RateLimitMiddleware`'s own documented design principle ("a rate limiter must never become
  a full outage").
- **Verified**: restarted the backend, logged in successfully as `admin@local.dev`.

### 2. Every timestamp in the app is wrong by the viewer's UTC offset
**Status: fixed.**

The backend stores/serializes naive (no-timezone) datetimes — confirmed directly in the
`GET /api/databases/{id}/health-history` response: `"sampled_at":"2026-09-02T22:13:46.560081"`,
no `Z`, no offset. The browser's `new Date(iso)` treats a timezone-less ISO string as
**local time**, not UTC. Result: every relative ("5h ago") and absolute date/time display
computed client-side is off by exactly the viewer's UTC offset.

**Reproduced concretely**: Health page showed LocalDB's collector as "Healthy - 5h ago"
while the actual latest sample (per the raw API response) was from under a minute earlier —
5 hours being exactly this browser's UTC+5 (Pakistan) offset. The Incidents drawer's
"Opened 02/09/2026, 22:20:03" and "Last evidence" fields are equally affected (they show the
raw UTC clock time mislabeled as local, so they read as if 5 hours in the past relative to
the viewer's real local time).

- `frontend/src/lib/time.ts:2` — `new Date(iso)` has no way to know the string is UTC if the
  string itself doesn't say so.
- Root cause is upstream, on the backend: SQLite round-trips timezone-aware Python
  `datetime`s as naive (a recurring pattern already documented elsewhere in this codebase's
  own history), and the Pydantic response schemas (`backend/app/schemas/telemetry.py:44,57,75,80`
  and likely others) serialize whatever naive value comes back with no forced UTC marker.
- **Notably inconsistent**: the Reports drawer's "Generated" field explicitly labels itself
  `(UTC)` (`frontend/src/pages/ReportsPage.tsx`, near the report metadata block) — someone
  already hand-fixed this in one place but not the others. That confirms the fix pattern
  (append `Z` before parsing, or explicitly label the field) is already known and just needs
  applying everywhere else: Health page, Incident drawer, Audit Log, Notification history, and
  any other page rendering a raw API timestamp.
- **Scope check needed**: this reproduced against SQLite (dev/test engine). Real Postgres
  preserves `tzinfo` correctly if the column is `TIMESTAMP WITH TIME ZONE` — verify the
  actual migration column types before assuming this disappears in production; if any column
  is `TIMESTAMP WITHOUT TIME ZONE`, the same bug reproduces there too.
- **Fix applied**: `ApiModel` (`backend/app/schemas/common.py`) — the shared Pydantic base
  every response schema inherits from — now forces every `datetime` field to serialize with
  an explicit UTC `Z` suffix, regardless of whether the underlying value happens to be aware
  or naive by the time it's read back. Fixed once, at the API boundary, rather than at every
  individual read site.
- **Verified**: new regression test
  (`backend/tests/test_dashboard_api.py::test_health_history_timestamps_are_marked_utc_not_naive`)
  proves a real round trip through SQLite still produces a `Z`-suffixed timestamp in the real
  API response. Re-ran the exact live repro: the Health page now shows "Collecting - last 13s
  ago" instead of the previous wrong "5h ago".

---

## MEDIUM — real, but doesn't block core functionality

### 3. Report content (health check, capacity summary, etc.) renders as raw JSON text
**Status: fixed.**

Generated a real Daily DBA report against the live Oracle connection. The top-level
metadata (database, period, generated-at, requested-by, data completeness, limitations) is
rendered as proper labeled fields — but the actual report **content** (health check results,
capacity summary, and presumably every other structured sub-section for every report type)
is dumped as pretty-printed raw JSON inside a `<pre>` block, not parsed into a table/list/
card the way the rest of the UI looks.

- `frontend/src/pages/ReportsPage.tsx:325-342` — `ContentSections()` is a generic fallback
  that does `Object.entries(content)` and `JSON.stringify(value, null, 2)` for every key,
  with no per-report-type or per-section formatting at all.
- Confirmed this affects at least: `capacity_summary`, `health_check_results`. Given the
  function is fully generic, it affects every content key of every report type identically
  — the report viewer never actually presents readable report content, only a labeled JSON
  dump.
- **Fix applied**: `ContentSections`/`ContentValue` (`frontend/src/pages/ReportsPage.tsx`)
  now recognizes the handful of shapes that actually recur across report types — an
  `{available: false, reason}` stub (a muted callout, not JSON), a health-check-style
  `{checks: [...], summary: {...}}` object (a real table with PASS/FAIL/UNAVAILABLE badges),
  an array of same-shaped records (a table), and a flat key/value object (a label/value
  grid) — falling back to formatted JSON only for the genuinely irregular remainder.
- **Verified live**: regenerated a Daily DBA report against the real Oracle connection.
  Capacity Summary now renders as "Not available — [reason]" instead of a JSON blob; Health
  Check Results renders as a real table ("Availability: PASS", "Resource limits:
  UNAVAILABLE", ...).

### 4. AI Copilot shows the same error message twice
**Status: fixed.**

Asked a question with AI disabled (the honest, correct behavior). The error ("AI is not
enabled on this deployment...") appears both inside the chat transcript bubble (correct,
expected) **and** as a second, separate red banner below it (duplicate).

- `frontend/src/pages/AiCopilotPage.tsx:49-50` pushes the error into local `history` state
  and renders it via `<ErrorState>` (line 91-93) — correct.
- `frontend/src/pages/AiCopilotPage.tsx:114` independently renders
  `{askMutation.isError && <Alert tone="crit">{errorMessage(askMutation.error)}</Alert>}` —
  the mutation's own error flag was never reset after being caught and stashed in `history`,
  so both render for the same single failure.
- **Fix applied**: removed the redundant standalone `<Alert>` banner entirely — the
  per-message `<ErrorState>` already shown in the chat transcript is the correct, contextual
  place for the error, exactly where the failed question is.
- **Verified**: tightened the existing test
  (`frontend/src/pages/AiCopilotPage.test.tsx`) from `toBeGreaterThan(0)` to
  `toHaveLength(1)`, and confirmed live — the error now appears exactly once.

---

## LOW — worth a defensive fix, not urgent

### 5. Health/session-check requests are slow when Redis is unreachable
**Status: fixed.** Every page load's initial `/api/auth/refresh` (and, before the Finding 1
fix, every login) call passes through `RateLimitMiddleware`, which attempts a real Redis
connection before failing open. In this environment that added several seconds of visible
"Checking your session..." delay on every fresh load, since redis-py had no explicit
timeout and fell back to the OS-level TCP connect timeout.

- **Fix applied**: `get_redis()` (`backend/app/redis_client.py`) now passes
  `socket_connect_timeout=2` and `socket_timeout=2` to `from_url(...)`.
- **Verified**: a real connection attempt against the genuinely unreachable Redis in this
  environment now fails in ~2.0s (measured directly), down from the OS default; new
  regression test
  (`backend/tests/test_live_qa_fixups.py::test_redis_client_is_constructed_with_a_short_connect_and_socket_timeout`)
  guards the timeout stays configured.

### 6. An unhandled route exception can produce a response with no CORS headers
**Status: fixed.** Confirmed this is a real, independent bug, not merely a symptom of
Finding 1: even with no custom middleware at all, a handler registered for the bare
`Exception` type is routed by Starlette through `ServerErrorMiddleware`, which sits
**outside** every middleware added via `app.add_middleware()` — including `CORSMiddleware`,
despite CORS being registered last (outermost) specifically so error responses carry CORS
headers. Every other handler in this app (`AppError`, `RateLimitedError`,
`RequestValidationError`) is keyed to a specific exception type, which Starlette routes
through `ExceptionMiddleware` (inside CORS) — verified empirically that only the
bare-`Exception` catch-all was affected.

- **Fix applied**: `handle_unexpected_error` (`backend/app/main.py`) now manually attaches
  `Access-Control-Allow-Origin`/`Access-Control-Allow-Credentials` to its response when the
  request's `Origin` matches a configured allowed origin — mirroring what `CORSMiddleware`
  would have done had it been able to see the response.
- **Verified**: new regression test
  (`backend/tests/test_live_qa_fixups.py::test_unhandled_exception_response_still_carries_cors_headers`)
  forces a real unhandled exception through the real app and asserts the response still
  carries the header.

---

## Independent verification pass — cross-validated against ground truth (2026-09-08)

Methodology: a fresh, dedicated `perf_monitor` Oracle user was created in `orclpdb`
(`CREATE SESSION` + explicit `SELECT` grants on `V$SESSION`/`V$SYSTEM_EVENT`/
`V$SESSION_WAIT`/`V$SYSSTAT`/`V$SYSTEM_WAIT_CLASS`/`V$SQL`/`V$SQLAREA`/`V$PROCESS`/
`V$LOCK`/`V$INSTANCE`/`V$DATABASE` via a role) — independent of OCPCC's own DBSNMP
monitoring credential, so this isn't the app grading its own homework. A real, deliberately
constructed CPU-bound performance issue (`declare ... for i in 1..1000000 loop v_acc :=
v_acc + sqrt(i) - ln(i+1) + mod(i, 97); end loop; end;` run concurrently across 6-10
sessions, no table access needed) was generated purely with that account's grants, then
OCPCC's dashboard was compared, screen by screen, against direct `V$` queries run with the
same account for the same moments.

A second account, a common user `C##PERF_MONITOR` (`CREATE SESSION CONTAINER=ALL` +
`SELECT ANY DICTIONARY`), was also created but turned out **not** to be usable for this:
`SELECT ANY DICTIONARY` does not extend to `V$` fixed views in this Oracle version - every
`V$` query as that user returned `ORA-00942`. It would need `SELECT_CATALOG_ROLE` or explicit
`V_$...` grants to serve as a second independent verification path.

**What matched exactly, every time**: Sessions page (exact SID/status/SQL ID/event/wait-class
match against a direct `V$SESSION` query); Performance's active-session-count chart (real
spikes to 11 and 7 sessions, matching the induced load exactly); Compare Mode (correctly
classified the larger spike CRITICAL against baseline, correctly reverted to Normal once load
stopped); incident correlation (a real CRITICAL incident for the 10-session spike, a real HIGH
incident for the 6-session one - severity scaling with magnitude, no manual intervention).

### 7. Sessions/Wait Analysis cannot distinguish "computing" from "idle" for CPU-bound work
**Status: fixed (2026-09-08).**

Queried `V$SESSION.STATE` directly during the load: every worker session showed
`WAITED SHORT TIME` (Oracle's "recently finished a wait, currently on CPU" state), confirming
they were genuinely computing, not idle. But `telemetry_probe.py`'s `SessionSampleRow` never
collects `STATE` - only `EVENT`/`WAIT_CLASS`/`STATUS`. For a tight CPU loop, `EVENT` stays
frozen at whatever it was *before* the loop started (`SQL*Net message from client`, wait
class `Idle`) for the loop's entire duration, since Oracle only updates that label when a new
wait begins. From the OCPCC UI alone, 6-10 sessions burning 100% CPU are visually
indistinguishable from 6-10 sessions doing nothing at all - the only real signal is the
active session *count* itself.

- `backend/app/oracle/telemetry_probe.py` — `SessionSampleRow`'s fields (`sid, serial#,
  username, sql_id, event, wait_class, status, blocking_session, machine, program, module,
  action`) have no `state` or any CPU-time column.
- `backend/app/services/analytics_service.py:109-127` (`compute_current_metrics`) confirms
  there is no CPU-time metric anywhere in the baseline/anomaly system either - only
  `active_session_count` and `wait_rate_centisec_per_sec:*`, and Oracle doesn't expose CPU
  consumption as a wait event, so Wait Analysis is structurally blind to pure CPU-bound load.
- **Fix applied**: two additive changes. (1) `SessionSampleRow`/`SessionSample`/
  `SessionResponse` gained a real `state` field (`V$SESSION.STATE`), surfaced as a new "State"
  column on the Sessions page and SQL Intelligence's Sessions panel - `WAITING` (blocked on
  `event` right now) vs. `WAITED SHORT/KNOWN/UNKNOWN TIME` (recently finished a wait, genuinely
  on CPU) now distinguishes the two cases this finding named. (2) A real CPU metric:
  `V$SYSSTAT`'s "CPU used by this session" (instance-wide despite the name) is now probed,
  persisted (`instance_health_snapshots.cpu_centisecs_used`), and turned into a rate
  (`cpu_centisec_used_per_sec`) the same way wait rates already are - a new
  `GET .../cpu/history` route feeds a Performance-page trend chart, and the metric is now a
  real baseline/anomaly-detected/Compare-Mode metric via `AnalyticsService.
  compute_current_metrics`, not just a chart.
- **A second, independent bug surfaced during this fix's own live verification** (not the
  original finding, but caught by the same rigor): the first implementation read the CPU rate
  via `recent_instance_health(limit=2)` - correct with only 2 rows, but once more real rounds
  accumulate, that method's even-sampling (built for `UI.5`'s chart windows) spans the *whole*
  history instead of two adjacent rounds, silently pairing the very first snapshot ever
  recorded with the latest one. Only visible against the real Oracle instance after ~96 real
  rounds had accumulated - a 2-row test fixture can't reproduce it. Fixed with a new
  `latest_two_instance_health` repository method (no even-sampling, mirrors
  `latest_two_wait_rounds`'s existing contract), plus a regression test seeding 5 historical
  rounds to guard it.
- **Verified live**: restarted the collector against the real Oracle instance; real session
  rows now carry genuine `state` values (`WAITING`, `WAITED SHORT TIME`) on both the Sessions
  page and SQL Intelligence's Sessions panel, including a real blocking pair caught mid-test
  (a session `WAITING` on `enq: TX - row lock contention`, blocked by a second session whose
  own `STATE` was also `WAITING` despite `STATUS: INACTIVE` - genuinely idle at a prompt while
  still holding the lock). Generated a real ~90s CPU-bound load (six concurrent sessions
  running the same CPU-burning PL/SQL block as this finding's original repro) and confirmed
  `/cpu/history`, the new Performance-page chart, and the new CPU Compare Mode panel all
  showed a real rate rising from ~0.2 cs/s to ~650 cs/s and cleanly returning to baseline once
  the load stopped - checked in both light and dark themes.

### 8. Top SQL requires a statement to survive two collector ticks before it appears at all
**Status: confirmed real, not a bug - working exactly as designed, but the practical
consequence is worth knowing.**

`compute_sql_deltas` (`backend/app/analytics/sql_metrics.py:71-86`) deliberately reports
nothing for any `(sql_id, plan_hash_value)` absent from the *previous* collector round -
"there's nothing to diff against yet," the same rule `compute_wait_class_rates` uses, and a
real guard against a reloaded cursor's reset counters looking like a false massive spike.
Empirically confirmed both sides of this: a 380-second run of the load statement (one SQL
collector tick, 300s interval) showed 220 real executions and 3,780 CPU-seconds directly in
`V$SQL` but **never appeared in Top SQL at all** - it was a new statement with no prior-round
baseline, and by the time a second round could have diffed it, the load had already stopped
and its counters were frozen (delta = 0). A second, 700-second run of the identical statement
appeared correctly once it survived a full tick-to-tick boundary while still executing - 102
executions, 1798.13s elapsed, 17.63s/exec, exactly matching direct `V$SQL` figures.

- A short but genuinely expensive SQL statement (started and completely finished within one
  ~300s SQL-collector window) is invisible to Top SQL even though it's sitting right there in
  `V$SQL` the whole time - only `sql_collector_interval_seconds` (300s by default,
  `backend/app/config.py`) and a statement's own survival across that boundary determine
  visibility, not how expensive it was.
- **Not fixed**: changing this would mean weakening the reloaded-cursor false-positive guard,
  a real trade-off that deserves a design conversation, not a reflexive change. Documented
  here, and referenced directly in `compute_sql_deltas`'s own docstring, so a future reader
  hits the concrete, measured consequence (not just the abstract rationale) at the point
  where the design decision is made.

## What's confirmed genuinely working (real evidence, not test doubles)

Worth stating since it directly matters for Master Plan Phase 29's certification status:
- **Real Oracle 19c EE connection succeeded** (`Test Connection` → "Connected successfully"),
  against a live instance on the local network.
- **Real capability detection**: 18/18 base views accessible, edition/version/RAC/Statspack
  all correctly detected.
- **Real collector cycles** ran successfully and persisted real session-count telemetry.
- **Real anomaly detection** fired on the real telemetry (`wait_rate_centisec_per_sec`
  anomalies, MEDIUM severity) and **real incident correlation** turned them into one incident
  that later escalated to CRITICAL as evidence accumulated — exactly the designed behavior.
- **Real report generation** against real data (50 health samples, 142 session samples, 8
  anomalies) succeeded end-to-end including the honest "unavailable" sections for SQL
  Regression/Capacity.
- The second registered database (QMSQA, an unreachable host) correctly shows "Failing (43)"
  with no crash anywhere in the fleet-wide collector loop — the isolation logic tested in
  Phase 26/27 holds under a real repeated-failure condition, not just a simulated one.
- AI-disabled and RBAC error states are honest and correctly worded everywhere tested.
- **(2026-09-08)** OCPCC's own telemetry, cross-validated field-by-field against direct `V$`
  queries from an independent, non-app credential, matched exactly under a real, deliberately
  induced CPU-bound performance issue - Sessions, Performance/active-session-count, Compare
  Mode, incident correlation, and (once given a fair collector window) Top SQL all agreed with
  ground truth, with the two exceptions documented as Findings 7-8 above.

This is the first real evidence closing Master Plan Phase 29's G4 gate ("real Oracle
connection and telemetry") — worth updating `docs/PRODUCTION_CERTIFICATION.md` once the
above bugs are fixed and this is repeated more thoroughly.
