# Runbook

## Prerequisites

- Docker + Docker Compose (primary way to run the stack)
- For running things outside Docker: Python 3.12, Node.js 22+

## First run

```bash
cp .env.example .env
```

Edit `.env` and set real values for `JWT_SECRET_KEY` and `SECRET_ENCRYPTION_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Optionally set `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD` to have the first ADMIN
account created automatically on boot (only happens if that email doesn't already exist).

```bash
docker compose up --build
```

This starts Postgres, Redis, the backend (runs `alembic upgrade head`, then
`python -m app.seed`, then starts uvicorn with `--reload`), and the frontend dev server.

- API: http://localhost:8000/api/health, http://localhost:8000/api/ready, http://localhost:8000/docs
- Frontend: http://localhost:5173

## Running things outside Docker

Backend:

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
cp ../.env.example .env   # then point DATABASE_URL/REDIS_URL at wherever Postgres/Redis actually run
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Tests

Backend unit + component tests (SQLite/fakeredis doubles - no external services needed):

```bash
cd backend
python -m pytest -q
python -m ruff check app tests --line-length 110
python -m mypy app --ignore-missing-imports
python -m bandit -q -r app
python -m pip_audit -r requirements.txt
```

Cross-cutting integration tests against a **real** Postgres + Redis (skips automatically if
neither is reachable):

```bash
docker compose up -d postgres redis
DATABASE_URL=postgresql+asyncpg://ocmd:<your-password>@localhost:5432/ocmd \
REDIS_URL=redis://localhost:6379/0 \
PYTHONPATH=backend python -m pytest tests/integration -q
```

Frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

End-to-end (Playwright) - requires the full stack running via `docker compose up`, plus a
seeded admin account matching `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` (defaults match
`.env.example`'s `ADMIN_BOOTSTRAP_*`):

```bash
cd frontend
npx playwright install --with-deps chromium
npm run e2e
```

## Creating additional users

The API has no public self-registration route by design - accounts are created by an ADMIN
through **Administration → Users** in the UI, or `POST /api/admin/users`. Roles: `ADMIN`,
`DBA`, `OPERATOR`, `VIEWER`, `AUDITOR` (see `backend/app/seed.py` for the permission
grants each role holds).

## Connecting a real Oracle database

Once a database is registered (**Databases → Add Database**, or `POST /api/databases`),
its detail drawer's **Capabilities** tab (DBA/ADMIN only) offers two actions:

- **Test Connection** (`POST /api/databases/{id}/test-connection`) - attempts a real
  connection with the stored credentials via `python-oracledb`. Always returns 200 with
  `{ok, category, message}`; a failure is a normal diagnostic outcome, not a server error.
  `category` is one of `authentication_failed`, `unreachable`, `unknown_error` - the raw
  driver exception text is never returned to the client, only logged server-side.
- **Detect Capabilities** (`POST /api/databases/{id}/detect-capabilities`) - connects and
  probes *only* the fixed base-view allowlist in `backend/app/oracle/allowlist.py`
  (`V$SESSION`, `V$SQL`, `V$INSTANCE`, ... - never `DBA_HIST_*`, `V$ACTIVE_SESSION_HISTORY`,
  or any other Diagnostics/Tuning Pack-gated view). Persists detected version, edition
  (SE2/EE, parsed from the `V$VERSION` banner), RAC status, Statspack presence, and a
  per-view privilege report to `database_capabilities`, and returns 502 if the database
  can't be reached at all.

The monitoring account only needs `SELECT` on the views listed in `ALLOWLISTED_VIEWS` plus
`SELECT` on `PERFSTAT.STATS$SNAPSHOT` if Statspack is installed and you want it detected
(Enterprise Readiness Plan D.3 strengthened this check from "does a PERFSTAT user exist" to
"is `STATS$SNAPSHOT` actually queryable" - a lingering PERFSTAT user with no real schema
installed now correctly reports `statspack_available: false`) - it does not need
`SELECT_CATALOG_ROLE`, `DBA` privileges, or anything Diagnostics/Tuning Pack-related. The
privilege report in the Capabilities tab shows exactly which of the allowlisted views it can
and can't currently read, so missing grants are visible before you try to onboard collectors
against it.

No real Oracle instance was available to verify this against in the environment this was
built in - see Known Gaps below.

## The Oracle collector

Once a database is registered and `is_active`, the scheduled collector
(`backend/app/collectors/scheduler.py`, an in-process APScheduler job - no separate worker
process in this phase) polls it every `COLLECTOR_INTERVAL_SECONDS` (default 15s):

- Connects with the stored credentials.
- Samples `V$SESSION` (user sessions only - `session_samples`), `V$SYSTEM_EVENT` excluding
  idle waits (`wait_snapshots`, raw cumulative counters - the analytics engine below
  computes deltas/rates from these, not the collector), and `V$INSTANCE`/an active-session
  count (`instance_health_snapshots`).
- Updates `collector_status` (last run/success/failure, consecutive failures, duration) -
  visible via `GET /api/databases/{id}/collector-status` and as a badge on the Overview
  page ("Not yet collecting" / "Collecting - last Xs ago" / "Collection failing (N)").

One database's connection failure is caught and recorded per-database - it never stops the
scheduler or other databases' collection. Set `COLLECTOR_ENABLED=false` to disable polling
entirely (the backend test suite always does this).

No real Oracle instance was available to verify this against - see Known Gaps below.

## SQL Intelligence (Master Plan Phase 9, Enterprise Readiness Plan D.1)

A separate scheduled job (`SqlCollectorService`, `app/services/collector_service.py`'s
`run_sql_once_for_database`/`run_sql_once_all_active`) polls `V$SQL`/`V$SQL_PLAN` on its own,
slower interval - `SQL_COLLECTOR_INTERVAL_SECONDS` (default 300s), independent of the 15s
session/wait/health collector above. It has its own connection, its own try/except, and its
own `collector_status` fields (`last_sql_run_at`/`last_sql_success_at`/
`last_sql_error_category`) - a monitoring account missing `SELECT` on `V$SQL_PLAN` can never
abort session/wait/health collection, proven by
`test_sql_collector_connect_failure_does_not_affect_session_collection`. Within the SQL round
itself, top-SQL stats and the plan probe are in separate try/except blocks too: a failing
plan probe still lets stats persist ("partial capability, honestly recorded").

- **Grants needed**: `SELECT` on `V$SQL` (already allowlisted, previously unused) and
  `V$SQL_PLAN` (added to `app/oracle/allowlist.py`'s `ALLOWLISTED_VIEWS` for this phase - a
  base view, no Diagnostics/Tuning Pack license required, matching the SE2-licensing-safe
  posture of every other collector in this platform). Deliberately NOT queried:
  `V$SQL_BIND_CAPTURE` (captures literal application data - a real PII/exfiltration risk),
  `ACCESS_PREDICATES`/`FILTER_PREDICATES` (same risk, lower necessity), and
  `V$SQL_PLAN_STATISTICS_ALL` (usually empty without `STATISTICS_LEVEL=ALL` - would silently
  collect nothing, the worst failure mode).
- **Config**: `SQL_COLLECTOR_ENABLED` (default true, independent off-switch from
  `COLLECTOR_ENABLED`), `SQL_COLLECTOR_INTERVAL_SECONDS` (default 300),
  `SQL_TOP_N` (default 50, `1-500`), `SQL_CAPTURE_TEXT` (default true - set false to track,
  rank, and regression-detect every statement identically without ever storing its text, for
  an operator who cannot retain query text at all).
- **Top SQL**: cumulative `V$SQL` counters are stored as raw facts
  (`sql_stat_samples`, never a derived rate); `GET /api/databases/{id}/top-sql?sort_by=` ranks
  by real per-interval delta between the two most recent rounds - never a live Oracle query on
  read.
- **Plan history**: capture-on-change - one row per distinct `(sql_id, plan_hash_value)`
  (`sql_plan_snapshots`), `first_seen_at`/`last_seen_at` touched on repeat sightings rather
  than re-snapshotting an unchanged plan every round. `GET
  /api/databases/{id}/sql/{sql_id}/plan/{hash}` serves the persisted snapshot, so a plan
  remains viewable after its real cursor has aged out of Oracle's shared pool.
- **Regression detection**: a dedicated deterministic comparator
  (`app/analytics/sql_regression.py`), not the general hour-of-day baseline engine - the
  general engine needs 20 samples in the *same hour-of-day bucket* before it can fire, which
  is unworkable for dozens of ephemeral SQL_IDs. Two signals, both against a statement's own
  recent history (current = last 60 min, baseline = the preceding 24h, excluding the current
  window): `PLAN_CHANGE` (the baseline period's dominant plan hash isn't present in the
  current period) and `ELAPSED_PER_EXEC` (current/baseline elapsed-per-execution ratio crosses
  a threshold - `>=25` CRITICAL, `>=10` HIGH, `>=4` MEDIUM, `>=2` LOW). A `PLAN_CHANGE` in the
  same round suppresses a same-`sql_id` `ELAPSED_PER_EXEC` finding (one root cause, not two).
  An open `SqlRegressionEvent`'s severity only ever escalates on worse evidence, mirroring
  `IncidentService`'s own principle. This comparator's thresholds are proven only by unit test
  (`test_sql_regression_detection.py`) - a real 24-hour regression can't be observed live in
  one session; see the Known Gaps note below.
- **API**: `GET /api/databases/{id}/top-sql`, `.../sql/{sql_id}`,
  `.../sql/{sql_id}/plan/{plan_hash_value}`, `.../sql-regressions` (`database:view`, the same
  `available`/`reason` envelope as every other telemetry route). **UI**: the SQL Intelligence
  page (`requiresPermission: database:view`) - sortable top-SQL table, an open-regression
  banner, and a drawer with full SQL text, plan history, and an indented plan-operation tree.

The Oracle probe and repository layer were verified live against the real orclpdb instance
(A.4) in this phase; the regression comparator's thresholds were not, for the reason above.

## Capacity / Tablespace Collector (Master Plan Phase 5, Enterprise Readiness Plan D.2)

A third separate scheduled job (`app/services/collector_service.py`'s
`run_capacity_once_for_database`/`run_capacity_once_all_active`) polls tablespace space usage
on its own, much slower interval - `CAPACITY_COLLECTOR_INTERVAL_SECONDS` (default 3600s/1h),
independent of both the 15s main collector and the 300s SQL collector. Same own-connection,
own-failure-isolation shape as the SQL collector: the outer probe (`DBA_TABLESPACES` +
`DBA_DATA_FILES` + `DBA_FREE_SPACE`, covering PERMANENT and UNDO tablespaces) must succeed for
anything to persist; a *nested* probe (`DBA_TEMP_FILES` + `V$TEMP_SPACE_HEADER`, TEMPORARY
tablespaces) is best-effort - a missing grant on either TEMP-specific view still lets
PERMANENT/UNDO data persist ("partial capability, honestly recorded").

- **Grants needed**: `SELECT` on `DBA_TABLESPACES`/`DBA_DATA_FILES` (already allowlisted from
  an earlier phase) plus `DBA_FREE_SPACE`/`DBA_TEMP_FILES` (added to
  `app/oracle/allowlist.py`'s `ALLOWLISTED_VIEWS` for this phase - base data-dictionary views,
  no Diagnostics/Tuning Pack license required) and `V$TEMP_SPACE_HEADER` (already
  allowlisted). Deliberately NOT queried: `V$UNDOSTAT` (undo *activity/tuning* metrics -
  generation rate, tuned retention - a different question from "will this run out of space,"
  out of this phase's scope) and `V$ASM_DISKGROUP` (not allowlisted - a different
  licensing/architecture question).
- **Config**: `CAPACITY_COLLECTOR_ENABLED` (default true, independent off-switch),
  `CAPACITY_COLLECTOR_INTERVAL_SECONDS` (default 3600), and four operator-tunable alert
  thresholds - `CAPACITY_WARN_PCT_USED`/`CAPACITY_CRITICAL_PCT_USED` (default 85.0/95.0) and
  `CAPACITY_WARN_DAYS_TO_FULL`/`CAPACITY_CRITICAL_DAYS_TO_FULL` (default 30/7) - since "how
  full is too full" varies far more by environment than SQL regression's ratio ladder does.
- **Current usage**: unlike top-SQL, a single collection round is enough - %used is a
  snapshot fact (`used_bytes`/`allocated_bytes`), not a delta between cumulative counters like
  `V$SQL`'s executions. `used_bytes` for PERMANENT/UNDO tablespaces is `allocated - free`
  (from `DBA_FREE_SPACE`); for TEMPORARY tablespaces it's read directly from
  `V$TEMP_SPACE_HEADER.BYTES_USED`, deliberately never cross-referenced against a separately-
  read `DBA_TEMP_FILES.BYTES` for the *allocated* figure too - live testing against orclpdb
  found the two can genuinely disagree at the same instant (Oracle doesn't always shrink a
  tempfile's high-water-mark back down after a large sort), so both allocated and used bytes
  for TEMP come from the one view, guaranteeing used ≤ allocated by construction.
- **Growth rate and time-to-full**: a deterministic linear least-squares fit of `used_bytes`
  vs. elapsed time over the trailing 7 days (`app/analytics/capacity_metrics.py`,
  `MIN_SAMPLES_FOR_TREND=3`) - not a statistical forecast model. Flat/shrinking usage or an
  unknown ceiling (autoextend enabled with `MAXSIZE` effectively unlimited - represented as
  `max_bytes=NULL`, never a fabricated huge number) both report `projected_days_to_full=None`
  with an explicit reason, never a fabricated or divide-by-zero ETA.
- **Alerting**: a comparator (`app/analytics/capacity_alerts.py`) raises two *independent,
  non-suppressing* finding kinds against the configured thresholds - `PCT_USED` and
  `PROJECTED_FULL` - unlike SQL regression's plan-change/elapsed-per-exec suppression, since a
  tablespace can be nearly full but flat (no growth alert needed) or well under threshold but
  growing fast (worth flagging early): two separately meaningful facts. An open
  `CapacityAlertEvent`'s severity only ever escalates on worse evidence, same principle as
  `SqlRegressionEvent`. This comparator's thresholds are proven only by unit test
  (`test_capacity_alerts.py`) - a real multi-day tablespace growth trend can't be observed
  live in one session; see the Known Gaps note below.
- **API**: `GET /api/databases/{id}/capacity`, `.../capacity/{tablespace_name}`,
  `.../capacity-alerts` (`database:view`, the same `available`/`reason` envelope as every
  other telemetry route). **UI**: the Capacity page (`requiresPermission: database:view`) -
  a tablespace table sorted worst-first, an open-alert banner, and a drawer with usage,
  growth explanation, open alerts, and recent history per tablespace.

The Oracle probe and repository layer were verified live against the real orclpdb instance
(A.4) in this phase (and a real bug in the TEMP-tablespace query was found and fixed during
that verification - see above); the growth-rate/alert comparator's thresholds were not, for
the reason above.

## Statspack Integration (Master Plan Phase 11, Enterprise Readiness Plan D.3)

Unlike every other collector above, this platform never *creates* Statspack snapshots -
Statspack is Oracle's own free, bundled historical-performance repository (the `PERFSTAT`
schema), populated only by the DBA's own independently-scheduled `statspack.snap` job
(typically hourly, sometimes not run at all). "Integration" means: detect whether a database
has Statspack actively snapshotting, incrementally sync newly-appeared snapshots into this
platform's own Postgres tables, and compute top-wait-class/top-SQL deltas between the two
most recently *synced* snapshots - which can be minutes or days apart, at the DBA's own
discretion, not this platform's fixed collection cadence.

- **Detection**: `statspack_available` (part of the existing Capabilities probe, `POST
  /api/databases/{id}/detect-capabilities`) was strengthened in this phase from "does a
  PERFSTAT database user exist" to "is `PERFSTAT.STATS$SNAPSHOT` actually queryable" - the
  old check could report `true` for a database with a lingering PERFSTAT user but no real
  schema installed. Only this on-demand action ever writes the capability row - the sync job
  below only ever reads it, never writes it, so the two can't race on the same row. A DBA
  installing Statspack later means re-running Detect Capabilities to pick it up (no
  scheduled re-probe in this phase).
- **Grants needed**: `SELECT` on `PERFSTAT.STATS$SNAPSHOT`/`STATS$SYSTEM_EVENT`/
  `STATS$SQL_SUMMARY` (added to `app/oracle/allowlist.py`'s own `STATSPACK_ALLOWLISTED_TABLES`
  tuple, kept separate from `ALLOWLISTED_VIEWS` since these are ordinary PERFSTAT-owned
  tables, not `V$`/`DBA_*` views). Statspack itself is a free, bundled Oracle feature
  requiring no Diagnostics/Tuning Pack license - unlike the `DBA_HIST_*`/
  `V$ACTIVE_SESSION_HISTORY` (AWR/ASH) family already blocklisted. Deliberately never
  queried: `STATS$SQLTEXT` (a CLOB-assembly table - `sql_text` for a Statspack-observed
  statement is instead cross-referenced against this platform's own D.1 SQL Intelligence
  `SqlStatement` table when the two happen to track the same `sql_id`), and
  `STATS$SYSSTAT`/`STATS$WAITSTAT`/latch/enqueue tables (out of this phase's top-wait-
  classes/top-SQL scope).
- **Config**: `STATSPACK_COLLECTOR_ENABLED` (default true, independent off-switch),
  `STATSPACK_COLLECTOR_INTERVAL_SECONDS` (default 7200/2h - slower than Capacity's 3600s,
  since polling more often than a typical DBA snap cadence wastes queries against PERFSTAT
  for no new data), `STATSPACK_MAX_SNAPSHOTS_PER_SYNC` (default 12, bounds one sync run's
  cost if a large backlog exists).
- **Sync**: the FIRST thing a sync attempt does is check `statspack_available` from Postgres
  only - if not `true`, it returns immediately without opening any Oracle connection, so a
  fleet where most databases don't have Statspack costs nothing extra. Otherwise: an
  incremental catch-up against a stored high-water-mark (`StatspackSyncState.
  last_synced_snap_id`), never a re-fetch of an already-synced snapshot. The snapshot-listing
  query must succeed for anything to persist; the wait-event and SQL-summary queries each
  have their own nested try/except (partial capability, same pattern as the SQL/Capacity
  collectors) - a missing grant on `STATS$SQL_SUMMARY` specifically still lets wait-event
  data persist.
- **Deltas**: `STATS$SYSSTAT`/`STATS$SYSTEM_EVENT`/`STATS$SQL_SUMMARY`'s counters are already
  cumulative-since-startup, exactly like `V$SYSSTAT`/`V$SQL`, so this phase reuses D.1's own
  `compute_sql_deltas`/`rank_sql_deltas` and the existing `aggregate_by_wait_class`/
  `compute_wait_class_rates` unmodified against Statspack-shaped rows (a thin adapter fixes
  `child_cursor_count` at 1, since `STATS$SQL_SUMMARY` has no per-child-cursor concept) -
  no new delta logic was needed. Rates are expressed per hour, not per second, since
  Statspack gaps can span days.
- **API**: `GET /api/databases/{id}/statspack` (`database:view`, the same `available`/
  `reason` envelope as every other telemetry route, Postgres-only reads) - honest, distinct
  reasons for "not installed", "installed but nothing synced yet", and "only one snapshot
  synced, need at least two." **AI tool**: `get_statspack_summary` - unlike `get_top_sql`/
  `get_tablespace_trend`, it stays a targeted, on-demand tool (like `get_sql_plan`), never
  auto-gathered into Copilot/RCA evidence, since a Statspack delta spanning days risks being
  presented as current evidence when auto-included. **UI**: an additive "Statspack" panel on
  the SQL Intelligence page, gated to render only when the selected database's
  `statspack_available` capability flag is `true` - deliberately not a dedicated nav item/
  page, since Statspack is an opt-in DBA feature likely present on only a minority of a
  fleet, and an always-empty page would be worse UX than a panel that only appears when
  relevant.

**Live-verification note**: no Oracle instance available in this environment has Statspack
installed (confirmed live against orclpdb - no `PERFSTAT` user, no `STATS$SNAPSHOT` table),
so only the honest "not installed" detection path was verified live; the sync/delta logic
itself is proven only by a fake-connector-driven test suite
(`backend/tests/test_statspack.py`), the same "unit-test-only, stated honestly" precedent
D.1/D.2 already set for logic that couldn't be observed live in one session.

## Change Intelligence (Master Plan Phase 14, Enterprise Readiness Plan D.4)

Configuration parameter drift detection: periodically snapshot `V$SYSTEM_PARAMETER` where
`ISDEFAULT = 'FALSE'` (only parameters the DBA has explicitly tuned away from Oracle's shipped
default - the large majority of an instance's ~300 parameters are never interesting), and diff
the two most recently collected rounds to surface parameters that were newly set, changed, or
reverted to default. Unlike Statspack, this needs no DBA-side opt-in or separate install - the
view is present on every edition, so this collector runs unconditionally for every registered
database.

- **Grants needed**: `SELECT` on `V$SYSTEM_PARAMETER` (added to `app/oracle/allowlist.py`'s
  `ALLOWLISTED_VIEWS` - a base dynamic-performance view, no Diagnostics/Tuning Pack license
  required). The existing capability probe already iterates `ALLOWLISTED_VIEWS` for its grant
  check, so `V$SYSTEM_PARAMETER` picked up a `privilege_report` entry with no new code.
  `V$SYSTEM_PARAMETER`, not the per-session `V$PARAMETER`, is used deliberately - it reflects
  the value in effect for the instance itself, never a per-session override.
- **Config**: `CONFIG_COLLECTOR_ENABLED` (default true, independent off-switch),
  `CONFIG_COLLECTOR_INTERVAL_SECONDS` (default 3600s, same tier as Capacity - configuration
  drift is an hours-to-days signal, but more time-sensitive than Statspack's DBA-controlled
  cadence, since `ALTER SYSTEM` can happen at any moment).
- **Collector**: the simplest of the four - one query (`V$SYSTEM_PARAMETER`), no capability
  pre-check (unlike Statspack, this view needs no gating), no partial-capability nested
  try/except (unlike SQL/Capacity/Statspack's 2-3 independent queries each). Persists a full
  snapshot of the non-default parameter set each round, like `TablespaceSample` - unlike
  Statspack's incremental sync, `V$SYSTEM_PARAMETER` has no natural high-water-mark column to
  sync from.
- **Diff**: a new, thin `app/analytics/config_metrics.py` set-diff (`compute_parameter_
  changes`) - the one place in D.1-D.4 that couldn't reuse D.1's own delta machinery, since a
  configuration round is a set of (name, value) pairs, not a numeric rate over a shared
  cumulative counter. Every transition (newly set, changed, reverted to default) falls out of
  simple set membership between two rounds.
- **API**: `GET /api/databases/{id}/configuration` (`database:view`, the same `available`/
  `reason` envelope as every other telemetry route, Postgres-only reads) - honest, distinct
  reasons for "no telemetry yet" and "only one round collected, need at least two." **AI
  tool**: `get_change_history` - stays a targeted, on-demand tool (like `get_sql_plan`/
  `get_statspack_summary`), never auto-gathered into Copilot/RCA evidence, since a
  configuration-drift lookback spans whatever gap exists between collection rounds and
  auto-including it risks presenting stale evidence as current. **Health check**: the
  `"configuration"` category (previously always `UNAVAILABLE`) is now real - `PASS` if the
  most recent round shows 0 changes, `WARN` if 1+ changes, no `FAIL` tier (a configuration
  change isn't inherently a failure severity, the same two-state shape as the `"locking"`
  category). **UI**: a new dedicated "Change Intelligence" page and nav item (not an additive
  panel like Statspack's) - since this collector runs unconditionally for every database
  rather than only a minority that opted in, a dedicated page is the right weight.

**Live-verification note**: unlike Statspack, this phase's detection/collection/API/health-
check/UI path was verified live end-to-end against the real orclpdb instance -
`V$SYSTEM_PARAMETER` is queryable and appears in the capability probe's `privilege_report`,
the collector persisted two real back-to-back rounds with 0 changes (nothing was altered), the
`"configuration"` health category correctly showed `PASS` in a freshly generated Health
report, and the frontend page rendered the real empty state. **Not verified live**: a genuine
parameter change being classified correctly (newly_set/changed/reverted_to_default) - proven
only by a fake-connector-driven test suite (`backend/tests/test_config_intelligence.py`),
since mutating orclpdb via `ALTER SYSTEM` would violate this platform's own design principle
of never modifying a monitored database itself, even for its own test purposes.

## The analytics engine

A second scheduled job (`AnalyticsService`, same interval, same scheduler) runs after the
collector and reads back what it just wrote - no Oracle connection of its own:

- **Current metrics**: `active_session_count` (latest `instance_health_snapshots` row), and
  `wait_rate_centisec_per_sec:<wait_class>` for each wait class (centiseconds waited per
  second, from the delta between the two most recent `wait_snapshots` rounds - a negative
  delta, meaning the instance restarted and the cumulative counter reset, is dropped rather
  than misreported as zero).
- **Baselines** (`metric_baselines`, one row per database/metric/hour-of-day): a rolling
  mean and variance via Welford's online algorithm - numerically stable, no need to retain
  raw history to keep updating it. Time-of-day bucketing means a metric's "normal" range for
  2am and for 2pm are tracked separately.
- **Anomalies** (`metric_baselines`... `anomalies`, `GET /api/databases/{id}/anomalies`): a
  z-score check runs *before* the current observation is folded into its baseline (so one
  spike can't dampen the very baseline it's being judged against). Needs
  `MIN_SAMPLES_FOR_ANOMALY` (20) prior observations in that hour-of-day bucket before it will
  flag anything - `app/analytics/baseline_engine.py` documents why, and the exact z-score
  severity thresholds (LOW/MEDIUM/HIGH/CRITICAL at 2/3/4/6 stddev) as tunable v1 defaults.

Anomalies aren't wired to notifications yet - that's a later phase. This phase only has to
prove the math and the data pipeline are correct, which it does with zero Oracle dependency
(`backend/tests/test_baseline_engine.py`, `test_analytics_metrics.py`,
`test_analytics_service.py` seed telemetry directly and drive the real service).

## Incident correlation

The moment `AnalyticsService` records an anomaly, it calls `IncidentService.correlate_anomaly`
in the same transaction (`app/services/incident_service.py`):

- **Correlation rule (v1)**: if the database already has a non-`RESOLVED` incident that
  received evidence within the last 30 minutes (`CORRELATION_WINDOW`), the new anomaly
  attaches to it as evidence instead of opening a second incident - the master plan is
  explicit that repeated symptoms of one event must not become separate incidents. A
  `RESOLVED` incident is never silently reopened by a later anomaly, even inside the window -
  resolving is a deliberate human signal. Anything more sophisticated (cross-database
  correlation, root-cause linkage) is a later, AI-assisted phase; this is the deterministic
  floor it builds on.
- **Lifecycle**: `OPEN` → `ACKNOWLEDGED` → `RESOLVED`, each transition recorded on the
  incident's timeline (`incident_events`) along with every evidence attachment and severity
  escalation (an incident's severity only ever escalates to match its worst evidence, never
  de-escalates on a milder one).
- **API**: `GET /api/incidents` (optional `status`/`monitored_database_id` filters),
  `GET /api/incidents/{id}`, `GET /api/incidents/{id}/timeline`,
  `POST /api/incidents/{id}/{acknowledge,assign,resolve}` (all three need `incident:manage`;
  `assign` validates the target user exists first). `GET /api/incidents/assignable-users`
  is a minimal id/name/email directory for populating an assignee picker, gated on
  `incident:manage` rather than `admin:users` - a DBA/OPERATOR who can resolve incidents may
  not hold user-administration rights.
- **UI**: the **Incidents** nav page is real now (not a placeholder) - list with
  severity/status filtering, a detail drawer with the timeline and (if you hold
  `incident:manage`) acknowledge/assign/resolve controls.

Acknowledge/assign/resolve are audited (`incident.acknowledged`/`incident.assigned`/
`incident.resolved`); evidence attachment and severity escalation are not (they're system-
generated analytics output, not a user or security action - the audit log stays focused on
who-did-what).

Zero Oracle dependency in this phase either - same as Phase 4, it only reacts to anomalies
already computed from data Phase 3 already collected.

## Email alerting

The moment an incident's severity first reaches HIGH or CRITICAL, `IncidentService`'s caller
(`AnalyticsService`) calls `NotificationService.notify_if_threshold_reached` (master plan
Sec 18's create → queue → process asynchronously → send → retry → record-status pipeline):

- **Dedup**: one notification per `(incident, severity)` pair - `dedupe_key` is a unique DB
  column, so even a race can't double-queue. Staying at the same severity with more evidence
  never re-alerts; escalating from HIGH to CRITICAL does (that's genuinely new information).
- **Recipients**: `default_recipients` get every HIGH/CRITICAL alert; `escalation_recipients`
  are added on top only for CRITICAL (master plan Sec 17's "configurable escalation"). No
  recipients configured → the attempt is skipped and logged, not silently dropped nor
  crashed.
- **Delivery**: a separate scheduler job (`NOTIFICATION_INTERVAL_SECONDS`, default 10s -
  faster than telemetry polling, since alert latency matters more) processes
  `PENDING`/`RETRYING` events. Failure sets `RETRYING` with exponential backoff
  (1m/5m/15m/1h) up to 5 attempts total, then `FAILED`. SMTP not configured at all fails
  immediately as `not_configured` rather than retrying forever.
- **The incident is never lost**: delivery status lives entirely in `notification_events`,
  separate from the `incidents` table - an incident's status/severity/timeline are untouched
  regardless of whether its alert email ever sends.
- **Content**: database name/edition/host, severity, incident title, opened time, and how
  much evidence has accumulated - deliberately *not* a probable cause, confidence, or
  recommended action, since there's no AI/RCA layer yet and master plan Sec 2/16 forbid
  fabricating that kind of certainty. No credentials ever appear in the email.
- **Configuration**: **Administration → Notifications** (`notification:configure`) - SMTP
  host/port/TLS/username/password (stored via the same SecretProvider as Oracle credentials,
  never returned by the API - only a `has_smtp_password` boolean), sender identity, and the
  two recipient lists. `GET /api/notifications/events` (optional `incident_id` filter,
  `incident:view`) is the delivery-status API the Incidents drawer's "Email notifications"
  section reads.

No real SMTP relay was available to verify actual delivery - `backend/tests/
test_notification_service.py` drives the real service (dedup, escalation, recipient
resolution, backoff timing, exhaustion, "incident survives total SMTP failure") against a
fake `EmailSender`, same test-double pattern as the Oracle connector. The real `send_email`
call (`app/notifications/mailer.py`, via `aiosmtplib`) itself is unexercised - configure a
real SMTP relay and trigger a HIGH/CRITICAL incident before trusting actual delivery.

## AI gateway and agents

Off by default (`AI_ENABLED=false`) - no local model is assumed to exist. To enable, point
`AI_BASE_URL` at any OpenAI-compatible endpoint (vLLM/Ollama/llama.cpp all support this) and
set `AI_PRIMARY_MODEL`/`AI_FALLBACK_MODEL` (default `gpt-oss-120b`/`gpt-oss-20b` per master
plan Sec 5). `app/ai/gateway.py`'s `OpenAICompatibleClient` tries the primary model first and
falls back to the secondary on any failure - swapping models or serving engines never touches
call sites elsewhere in the app.

- **The tool layer is the trust boundary, not the model** (`app/ai/tools.py`). Every tool
  reads only this platform's own Postgres repository - there is no code path from here to an
  Oracle connection, an Oracle credential, or arbitrary SQL. Each tool returns
  `{"available": false, "reason": ...}` when data is legitimately missing rather than an
  empty result that could be misread as "nothing's wrong." `get_top_sql`, `get_sql_plan`,
  and `get_sql_regressions` (Enterprise Readiness Plan D.1) are real now, backed by the SQL
  Intelligence collector below - `get_top_sql`/`get_sql_regressions` are gathered
  automatically for any Copilot/RCA query scoped to a database; `get_sql_plan` needs a
  specific `sql_id` and stays a targeted, on-demand tool to protect the prompt budget.
  `get_tablespace_trend` (Enterprise Readiness Plan D.2) is real too, backed by the Capacity
  collector below, and gathered automatically for both Copilot and RCA (unlike `get_sql_plan`,
  tablespace exhaustion is a plausible causal contributor to an incident, so it isn't
  targeted-only). `get_statspack_summary` (Enterprise Readiness Plan D.3) is real too, backed
  by the Statspack sync below - it stays a targeted, on-demand tool like `get_sql_plan`
  (unlike `get_top_sql`/`get_tablespace_trend`), since a Statspack delta can span days at the
  DBA's own discretion and auto-including it risks presenting stale evidence as current.
  `get_change_history` (Enterprise Readiness Plan D.4) is real too, backed by the Change
  Intelligence collector below - it also stays a targeted, on-demand tool: a configuration-
  drift lookback spans whatever gap exists between collection rounds, so auto-including it
  risks the same stale-evidence problem. Master plan Sec 9 names one more tool
  (`get_host_metrics`) that is still deliberately NOT implemented - its collector doesn't
  exist, and a tool that always returns empty/fake data would be exactly the
  fabricated-evidence problem Sec 2 forbids.
- **Tool selection is deterministic, not LLM-driven**, in this phase - `CopilotService`
  gathers evidence based on the `database_id`/`incident_id` the caller provides, not a
  model-initiated function-calling loop. There's no real local model available here to
  validate function-calling reliability against, so only the synthesis into a natural-
  language answer goes through the model; what evidence it sees is fixed and auditable.
- **Prompt-injection defense**: evidence (some of it - session `program`/`module`/`username`,
  and now SQL text/plan object names from `get_top_sql`/`get_sql_plan` - originates from the
  monitored Oracle instance, not a trusted source) is explicitly labeled as data to reason
  about, never as instructions, in every system prompt (`app/ai/prompts.py`). SQL text is by
  far the largest attacker-controllable string this platform puts in a prompt; see
  `backend/tests/test_adversarial_qa.py`'s SQL-text injection test.
- **AI Copilot**: `POST /api/ai/ask` (`ai:query`) - `{question, database_id?, incident_id?}`.
  Answers are grounded only in the gathered evidence; no context returns "I don't have
  anything to investigate" rather than calling the model at all.
- **RCA**: `POST /api/incidents/{id}/rca` (`ai:query`) - the model is asked for a strict JSON
  object (`probable_root_cause`, `confidence`, `evidence_summary`, `secondary_contributors`,
  `alternative_hypotheses`, `recommended_investigation`, `recommended_action`, `risk`,
  `data_completeness_note` - master plan Sec 16). If the response can't be parsed, or
  confidence isn't one of low/medium/high, the result comes back honestly empty
  (`data_completeness_note` explains why) rather than guessing. `recommended_action` is
  always a suggestion for a human to evaluate - this platform performs no automated
  remediation, and the RCA prompt says so explicitly.
- Both endpoints return 502 with a clear message when `AI_ENABLED=false` or the gateway is
  unreachable, and both audit every call (`ai.copilot_query`/`ai.rca_requested`) with the
  tools used and a truncated question preview - never the full answer text.

No local LLM server was available to verify actual model responses. The tool layer
(`backend/tests/test_ai_tools.py`), the RCA JSON parser's defensive handling of garbage/
markdown-fenced/invalid-confidence output (`test_rca_service.py`), and both services' evidence
gathering, grounding, audit trail, and gateway-failure handling
(`test_copilot_service.py`, `test_rca_service.py`) are proven end-to-end against a fake
`LLMClient`, same test-double pattern as the Oracle connector and email sender - what's
unverified is specifically whether a real gpt-oss-120b/20b deployment follows the JSON-shape
instructions reliably enough for `parse_rca_response` to succeed in practice, and whether the
grounding rules hold up against an adversarial prompt from real monitored-database evidence.
Configure a real local model and test both before trusting this in production.

## Dashboard suite

Five pages replace their Sidebar placeholders, all behind `database:view`, all reading only
this platform's own Postgres telemetry (never a live Oracle query on page load):

- **Performance** (`/performance`) - `GET /api/databases/{id}/health-history?hours=`
  (0.25/1/6/24/168, capped at 720/30 days server-side) drives an active-session-count
  timeline; anomalies in the same window are listed below it rather than overlaid on the
  chart, since the two series don't share timestamps and recharts' category axis doesn't
  align mismatched series cleanly.
- **Sessions** (`/sessions`) - `GET /api/databases/{id}/sessions` returns the latest
  V$SESSION-derived snapshot, with a blocked-by badge per row.
- **Wait Analysis** (`/wait-analysis`) - `GET /api/databases/{id}/waits` computes a
  centisec-waited-per-second rate per wait class as a delta between the two most recent
  collection rounds; `{"available": false, "reason": ...}` until there are two rounds to
  diff.
- **Blocking & Locks** (`/blocking-locks`) - `GET /api/databases/{id}/blocking` returns the
  current blocking chains, flagging a blocker session that isn't present in the latest
  sample (`blocker_in_this_sample: false`) instead of guessing whether it's still blocking.
- **Health** (`/health`) - fleet-wide, not per-database-selected: collector status +
  capability-probe status across every registered database in one table, distinct from the
  per-database Capabilities tab on the Databases page.

`backend/app/telemetry_views.py` holds the `latest_round`/`build_blocking_chains` pure
functions shared between these endpoints and the Phase 8 AI tool layer
(`get_active_sessions`/`get_blocking_chain` in `app/ai/tools.py`) - one implementation, so
the dashboard and the AI Copilot can never disagree about what "the latest round" or "who's
blocking whom" means.

All five pages poll every 15s and render an honest empty/unavailable state - no synthetic
data is ever substituted when the collector hasn't sampled yet. `PerformancePage` and
`WaitAnalysisPage` (the two recharts consumers) are route-level `React.lazy()` chunks
(`frontend/src/App.tsx`) so recharts' ~99kB gzipped weight only loads for someone who visits
one of those two routes.

Zero Oracle dependency in this phase - same as Phase 4/5, it only reads history Phase 3
already collected into Postgres.

## Reports (Master Plan Phase 21)

`POST /api/reports` (`report:create`) generates one of six report types and persists it:

- `DAILY_DBA` / `PERFORMANCE` - `database_id` required, `period_start`/`period_end` optional
  (default: last 24h; capped at 30 days span).
- `INCIDENT` - `incident_id` required; pass `include_rca: true` to also invoke the AI gateway
  (labeled a hypothesis, not authoritative telemetry, in the report content) - off by default
  since it costs an LLM call and requires `AI_ENABLED`.
- `SQL_REGRESSION` - `database_id` required; returns real top-SQL and open/resolved
  regression content (Enterprise Readiness Plan D.1) once this database has any SQL
  telemetry, otherwise `data_available: false` with an honest reason - a data-availability
  fact, not a feature-doesn't-exist fact.
- `CAPACITY` - `database_id` required; returns real per-tablespace usage, growth-rate, and
  open/resolved alert content (Enterprise Readiness Plan D.2) once this database has any
  capacity telemetry, otherwise `data_available: false` with an honest reason - a
  data-availability fact, not a feature-doesn't-exist fact.
- `HEALTH` - `database_id` required; runs `app/reports/health_check.py`'s 11-category
  deterministic engine (availability, resource limits, storage, performance, SQL regression,
  locking, operational, backup, Data Guard, ASM, configuration - the exact set master plan
  Sec 17 names). Availability, locking, performance, SQL regression (Enterprise Readiness
  Plan D.1), and storage (Enterprise Readiness Plan D.2) are backed by a real collector
  today; the other 6 always report `UNAVAILABLE` with a reason, never a false `PASS`.

`GET /api/reports` lists/filters (`report_type`, `database_id`, `incident_id`);
`GET /api/reports/{id}` views one in full; `GET /api/reports/{id}/export?format=csv|pdf`
downloads it. Both export formats are built in memory and streamed directly into the HTTP
response - never written to a filesystem path, so there is no server-side filename derived
from client input and no path-traversal surface on export. PDF text (incident titles,
resolution notes, anything sourced from user or Oracle-originated free text) is XML-escaped
before reaching reportlab's Paragraph markup parser. Every generation and export is audited
(`report.generated` / `report.exported`, target_type `report`).

The **Reports** page (real, replaces its placeholder) provides Generate (type + scope +
period filters), a filterable list, a View drawer (full content, `data_completeness_note`,
`limitations`), and CSV/PDF Download buttons - all real HTTP calls with loading/success/
error/empty states, no decorative controls.

## Fleet management (Master Plan Phase 22)

- **Tags**: freeform strings on `MonitoredDatabase.tags`, normalized (lowercased/trimmed/
  deduped, max 20 tags of 50 chars each) at the schema layer on both create and update. `PUT`
  with `tags: []` clears them; omitting `tags` leaves them unchanged.
- **DB groups**: `DatabaseGroup` + a `DatabaseGroupMembership` many-to-many join (a database
  can belong to any number of groups) - `GET/POST /api/database-groups`,
  `PATCH/DELETE /api/database-groups/{id}`, and `GET/PUT /api/databases/{id}/groups` (the PUT
  replaces the full membership set, matching the checkbox-list UI in the Databases page's new
  **Groups** tab). Both are gated on the existing `database:view`/`add`/`edit`/`delete`
  permissions - no new permission was needed.
- **Fleet filters**: `GET /api/databases?group_id=&tag=` and
  `GET /api/incidents?group_id=&tag=` (the "global incident view" - already fleet-wide by
  default, these just narrow it to one segment). `monitored_database_id` on the incidents
  endpoint takes precedence over `group_id`/`tag` if given together. Tag filtering happens in
  Python after a relational fetch, not a JSON-containment SQL operator, since JSON query
  syntax differs between SQLite (tests/local dev) and Postgres (production) and this
  platform's JSON columns must stay portable between them.
- **Bulk onboarding**: `POST /api/databases/bulk` (`database:add`), up to 100 rows per call,
  each going through `DatabaseService.create_database()` - the identical single-add path,
  including secret storage and audit. One row's failure (typically a duplicate name) is
  reported per-row and never aborts the batch. The Databases page's **Bulk Add** button opens
  a JSON-paste modal showing per-row created/failed results.
- **Fleet Overview** (`/`, the Overview page): group/tag filter controls, and each database
  card now shows its open-incident count as a link into `/incidents?database_id=...` - the
  Incidents page reads that query param on load to pre-scope an otherwise-global view. This
  is the master plan's Fleet Overview "DB filter" and "incident drill-down" requirements.
- **A real, previously-invisible bug this phase surfaced and fixed**: SQLite does not enforce
  foreign keys (including `ON DELETE CASCADE`/`RESTRICT`) unless told to per connection -
  `app/db.py` now sets `PRAGMA foreign_keys=ON` for SQLite connections only (Postgres always
  enforces them). Turning that on immediately exposed that `DatabaseService.delete_database()`
  deleted a database's encryption secret *before* deleting the database row - under real FK
  enforcement that violates the credential's `RESTRICT` constraint on `secret_ref` and the
  delete would fail. This would have failed identically against a real Postgres database in
  production; it was invisible in this codebase's test suite purely because SQLite wasn't
  enforcing the constraint. Fixed by deleting the database row first (its own
  `ON DELETE CASCADE` removes the credential row), then the now-unreferenced secret.

## Integrations (Master Plan Phase 23)

Master plan Sec 36 Phase 23 prioritizes "Webhooks first, Slack/Teams next" - that is exactly
what's implemented; Jira/ServiceNow ticket creation is not (see Known Gaps).

- **Configuration**: `POST /api/integrations` (`notification:configure`) creates one channel
  - `type` is `WEBHOOK` (generic JSON, optionally HMAC-signed), `SLACK` (an incoming-webhook
  URL - no bot/OAuth app needed), or `TEAMS` (a classic Incoming Webhook connector URL,
  MessageCard format - Teams' newer Adaptive Card/Workflows path needs an app registration
  this platform doesn't have). Any number of integrations can be active at once (unlike
  NotificationSettings, which is a single SMTP profile). `trigger_severities` (default
  `["HIGH", "CRITICAL"]`) controls which incident severities fire it - `PATCH`/`DELETE
  /api/integrations/{id}` manage it afterward, `POST /api/integrations/{id}/test` fires one
  real synchronous delivery and reports success/failure in the body (200 either way - a
  failed test is an expected diagnostic outcome, not a server error, same contract as
  database Test Connection).
- **Delivery**: `app/services/integration_service.py` mirrors
  `app/services/notification_service.py`'s pipeline exactly - `notify_if_threshold_reached`
  queues one `IntegrationDelivery` row per (integration, incident, severity) the first time
  that threshold is reached (dedupe key `{integration_id}:{incident_id}:{severity}`, so a
  HIGH→CRITICAL escalation still fires its own delivery); a scheduler job
  (`_process_pending_integrations`, same `NOTIFICATION_INTERVAL_SECONDS` cadence) calls
  `process_pending()`, which retries `RETRYING` deliveries on the identical backoff schedule
  as email (`[60, 300, 900, 3600]`s, `MAX_ATTEMPTS=5`) via `app/integrations/webhook_sender.py`
  (a thin `httpx` wrapper, same mockable-`Protocol` pattern as `EmailSender`/`OracleConnector`).
  An incident is never affected by a delivery failing - it just sits in `RETRYING`/`FAILED`,
  visible via `GET /api/integrations/deliveries?incident_id=` (also surfaced in each
  incident's drawer, alongside its existing email status) or
  `?integration_id=` (surfaced as delivery history in Administration → Integrations).
- **Security**: `target_url` must be `http://`/`https://`, and (as of Master Plan Phase 24)
  is also checked against the SSRF guard below - a private/loopback/reserved target is
  rejected at create/update time. The signing secret is stored via the same `SecretProvider`
  as Oracle/SMTP credentials, `has_secret` only in every API response, never the plaintext.
  Every create/update/delete/test is audited (`integration.created`/`updated`/`deleted`/`tested`).

## Security hardening (Master Plan Phase 24)

See [docs/THREAT_MODEL.md](THREAT_MODEL.md) for the full trust-boundary/attacker-class/
control-inventory writeup. This section covers the concrete, configurable controls.

- **Rate limiting** - two layers, both Redis-backed (`app/services/rate_limit_service.py`,
  a fixed-window counter):
  - `RateLimitMiddleware` (`app/security/middleware.py`) applies a per-client-IP floor
    (`API_RATE_LIMIT_PER_MINUTE`, default 300/min) to every `/api/*` request except
    `/api/health`/`/api/ready`. Fails *open* (logs a warning, lets the request through) if
    Redis itself is unreachable - a rate-limit outage must never become a full API outage,
    same principle as SMTP/webhook/AI failures never blocking core function elsewhere.
  - `require_rate_limit(...)` (`app/security/deps.py`) is a per-route dependency keyed by
    *authenticated user id*, applied to the platform's most expensive operations: AI
    Copilot ask and RCA (10/min each), report generation (20/min), bulk database onboarding
    (5/min), and integration test-delivery (10/min). Both layers return `429` with a
    `Retry-After` header (`RateLimitedError` in `app/exceptions.py`) - login's own existing
    per-IP limit (30/5min) was also switched from a plain `401` to this same `429` +
    `Retry-After` contract for consistency.
- **Request body size limit** - `BodySizeLimitMiddleware` rejects a request whose declared
  `Content-Length` exceeds `MAX_REQUEST_BODY_BYTES` (default 5MB) with `413`, before any
  parsing occurs. Checks the declared header only - see Known Gaps.
- **Security headers** - `SecurityHeadersMiddleware` adds `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a
  restrictive `Permissions-Policy` to every response; `Strict-Transport-Security` is added
  only when `ENVIRONMENT=production`.
- **SSRF guard** - `app/security/ssrf_guard.py`'s `assert_target_url_is_safe()` resolves an
  `Integration.target_url`'s hostname and rejects it if it's private/loopback/link-local/
  reserved/unspecified (or the hostname `localhost`/`metadata.google.internal`, blocked
  without even attempting a lookup). DNS resolution is behind a mockable `HostResolver`
  Protocol (`resolve_host_real` is the default, a literal IP short-circuits with zero
  network I/O) - same test-double pattern as `EmailSender`/`WebhookSender`/`OracleConnector`,
  so tests never make a real socket call. `ALLOW_INTERNAL_INTEGRATION_TARGETS=true` is a
  dev/test escape hatch only - never set it in production.
- **Middleware ordering matters**: Starlette makes the *last*-added middleware the
  *outermost* layer. `app/main.py` adds `BodySizeLimitMiddleware` → `RateLimitMiddleware` →
  `SecurityHeadersMiddleware` → `CORSMiddleware`, so CORS is outermost (an OPTIONS preflight
  never reaches the rate limiter, and even a 429/413 error response still carries CORS
  headers - otherwise a browser reports a confusing generic network error instead of a
  readable message).

A real, previously-invisible bug this phase's own tests surfaced (worth noting for anyone
touching middleware/Redis again): `app.dependency_overrides[get_redis]` only intercepts
`get_redis()` when it's resolved via FastAPI's `Depends()` inside a route handler - it does
**not** intercept a plain function call from ASGI middleware (middleware has no dependency
injection). `RateLimitMiddleware` calling `get_redis()` directly was therefore trying to open
a real TCP connection to `redis://localhost:6379/15` on every request in tests, each one
stalling for several seconds before the middleware's fail-open handling kicked in. Fixed in
`tests/conftest.py` by also pre-seeding `app.redis_client._redis` (the module-level
singleton) with the fake instance directly, so *any* caller - `Depends()`-based or a plain
function call - gets it.

## Scale testing (Master Plan Phase 25)

Full methodology, results, and honest gaps are in
[docs/SCALE_TEST_REPORT.md](SCALE_TEST_REPORT.md) - this section is a pointer, not a
duplicate.

Run the benchmark yourself: `cd backend && python scripts/scale_bench.py` (under a minute;
writes `docs/scale_bench_results.json`). It seeds 10/50/100-database fleets and measures
collector overhead, analytics-engine overhead, fleet-list/incident-list API read latency,
on-disk storage growth, a 500-item notification-queue backlog drain, and AI Copilot
orchestration overhead under up to 50 concurrent requests - all against this platform's own
code paths (fake Oracle connector, fake LLM client), the same test-double pattern as
everywhere else in this codebase.

Headline results: per-database collector/analytics overhead stayed flat (~4.5-5ms) from 10
to 100 databases: fleet-list/incident-list API reads stayed roughly two orders of magnitude
under the master plan's own p95 < 1.5s target at every tested size; the notification queue
drained a 500-item backlog in exactly the expected number of ticks with nothing lost; AI
orchestration overhead scaled sublinearly with concurrency (50 concurrent requests took
under 8x a single request's time, not 50x).

One real, previously-invisible bug was found and fixed while building this benchmark: both
scheduler jobs that decide which databases get polled every interval
(`CollectorService.run_once_all_active` and `AnalyticsService.run_once_all_active`) called
`list_all(limit=1000)` - a fleet past 1000 databases would have silently stopped receiving
telemetry and analytics for everything past the 1000th, with no error or log line. Fixed via
a new `MonitoredDatabaseRepository.list_active_ids()` that pages internally with no cap, and
regression-tested at 650 databases in `backend/tests/test_scale_hardening.py`. A second,
smaller scale ceiling was found and *not* fixed this phase (see Known Gaps below):
`GET /api/databases` truncates past 200 databases - confirmed safe at every tested
10/50/100 tier, but real for a fleet larger than that.

This environment has no reachable Postgres or Redis server (both time out on connect), so
every number in the report reflects SQLite-on-disk plus an in-process `fakeredis` queue in a
single process, not the production stack under real concurrent worker processes - see the
report's own Methodology section before using these numbers for capacity planning.

## DR / recovery (Master Plan Phase 26)

Full detail and operator-facing procedures are in
[docs/DR_RUNBOOK.md](DR_RUNBOOK.md) - this section is a pointer, not a duplicate.

The five scenarios the master plan names (Oracle unreachable, collector/API process
restart, PostgreSQL outage, queue recovery, notification provider outage) are each either
fully automatic already or documented as a manual infrastructure-layer procedure - there is
no self-healing "click to recover" action anywhere in the product, consistent with the
platform's own no-automated-remediation scope rule.

A real bug was found and fixed while proving queue recovery:
`NotificationService.process_pending()` and `IntegrationService.process_pending()` used to
commit their whole batch (up to 50 items) in one transaction at the very end - a process
killed mid-batch, after some emails/webhooks had already gone out over real SMTP/HTTP but
before the commit recording them as sent, would resend those same alerts on the next tick.
Both now commit after every individual item
(`backend/app/services/notification_service.py`, `backend/app/services/integration_service.py`),
so a mid-batch crash can only cost the one in-flight item's own progress. Proven with a
simulated process kill (an uncaught `SystemExit`, which - like a real OS-level kill - runs
no Python exception handler at all) in `backend/tests/test_dr_recovery.py`.

`GET /api/ready` was also proven to both correctly report *and* recover from a simulated
Postgres or Redis outage with no app restart required - readiness just reflects whatever the
next connection attempt finds.

## Adversarial QA (Master Plan Phase 27)

Full findings are in [docs/THREAT_MODEL.md](THREAT_MODEL.md)'s Sec 4-6 - this section is a
pointer, not a duplicate. Five audits ran against this codebase, each trying to actively
break a control built in an earlier phase rather than just confirm its happy path:

- **SQL injection**: the one raw-SQL construction site in the whole codebase (a bandit-
  flagged `# nosec B608` in `app/oracle/capability_probe.py`) was confirmed safe - the
  interpolated value is always drawn from a hardcoded allowlist, never user input. A new
  regression test (`backend/tests/test_adversarial_qa.py`) proves the allowlist gate itself
  rejects a malicious view name, closing the "no test proves this" gap the audit flagged.
- **Credential leakage**: found and fixed a real bug. `RequestValidationError`'s handler
  (`app/main.py`) echoed a field's raw submitted value back in 422 responses via Pydantic
  v2's `input` key - a password/secret field that failed validation (too long, wrong type)
  reflected the credential itself in the error body. Fixed by stripping `input` from every
  validation error uniformly.
- **SSRF**: adversarially tested six bypass techniques (DNS rebinding, HTTP redirects, IPv6
  encodings, alternate IP literal encodings, multi-address DNS responses, the dev-only
  `allow_internal` escape hatch) against the Phase 24 guard - five were already blocked by
  existing code. Added a second SSRF check in `app/integrations/webhook_sender.py` that fires
  immediately before every send, not just at integration create/update, narrowing (not
  eliminating - see docs/THREAT_MODEL.md Sec 5.4) the one confirmed residual gap.
- **RBAC bypass**: audited all 47 routes across every route file - none missing an
  authorization dependency, no permission-level mismatch. Surfaced and documented (not fixed
  - see Known Gaps below) an unexamined assumption: this platform has no multi-tenant
  isolation, so every `{id}`-scoped route is safe only under a one-organization-per-
  deployment model.
- **AI prompt injection**: confirmed the existing grounding-rules framing
  (`app/ai/prompts.py`) genuinely labels Oracle-sourced session/SQL data as untrusted, but it
  had never been tested against an actual adversarial payload. Added a regression test that
  plants an injected instruction into a session's module/program/machine field and proves the
  framing text survives intact and the payload stays confined to the labeled evidence block,
  never leaking into the system prompt.

## Release candidate (Master Plan Phase 28)

Full detail is in [docs/RELEASE_NOTES.md](RELEASE_NOTES.md),
[docs/UPGRADE_AND_ROLLBACK.md](UPGRADE_AND_ROLLBACK.md), and - the one to actually read before
trusting this release - [docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

This release is `v0.1.0-rc1`. The migration chain (9 revisions, single linear head) was
proven to upgrade and downgrade cleanly end-to-end against a scratch SQLite database this
cycle - `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade head`, no
errors at any step - not just assumed reversible because every migration file happens to
define a `downgrade()`. The same has not been proven against Postgres in this environment.

Two real CycloneDX SBOMs were generated from the actual locked dependency sets (not hand-
maintained): `docs/sbom-backend.json` (`pip-audit -f cyclonedx-json`, 54 components) and
`docs/sbom-frontend.json` (`@cyclonedx/cyclonedx-npm`, 340 components).

The most important finding this phase: **a real-infrastructure integration suite
(`tests/integration/`, 4 tests against actual Postgres 16 + Redis 7) already exists and is
already wired into `.github/workflows/ci.yml`, but has never executed even once** - this
repository has no connected git remote or CI runner. Same story for the one Playwright E2E
spec (`frontend/e2e/login-flow.spec.ts`) - authored, configured, never run (no browser
binaries installed in any environment this project has used). Neither gap needs new code;
both need infrastructure this development environment doesn't have, run once. See
`docs/PRODUCTION_READINESS.md` Sec 4 for exactly what running each would take.

## Production certification (Master Plan Phase 29)

Full gate-by-gate reasoning is in [docs/PRODUCTION_CERTIFICATION.md](PRODUCTION_CERTIFICATION.md)
- this section is a pointer, not a duplicate. **Decision: NOT CERTIFIED.**

Evaluated against the master plan's own eleven release gates (Sec 37, G0-G11): 5 pass
outright, 5 are partial, one fails - **G4 ("real Oracle connection and telemetry")**. No real
Oracle instance has ever been connected to this platform, at any point in its history; every
collector/capability/telemetry test runs against a fake connector. That single failure also
fails G11 ("any release blocker" is unconditional), so certification cannot be granted
regardless of how strong the other ten gates are.

The path forward needs no new code - it needs already-built code run against real
infrastructure, in priority order: (1) a real Oracle instance, for one real onboarding +
collector cycle; (2) `tests/integration/` against real Postgres + Redis (already wired into
CI, never executed); (3) a real SMTP relay, for one real alert email; (4) the Playwright E2E
spec, against a real deployed stack; (5) a real local LLM, for one real Copilot/RCA query
including the adversarial payload from Phase 27's own test suite; (6) a standalone UI
control-inventory pass (master plan Sec 34) - the one item on this list that needs
development time rather than infrastructure, and could be done without waiting on the rest.

## Troubleshooting

- **`/api/ready` returns 503**: check the `dependencies` field in the response body - it
  names which of Postgres/Redis is unreachable.
- **Login always fails**: confirm `python -m app.seed` actually ran (check backend
  container logs) and that `ADMIN_BOOTSTRAP_EMAIL`/`PASSWORD` were set in `.env` *before*
  the first boot - the bootstrap only fires once, when that email doesn't yet exist.
- **Account locked out**: 5 failed logins locks it for 15 minutes
  (`LOGIN_MAX_ATTEMPTS`/`LOGIN_LOCKOUT_MINUTES` in `.env`). An ADMIN cannot currently unlock
  an account early through the UI - wait out the window, or clear `locked_until` directly in
  the `users` table for local dev.
- **Alembic can't find `app`**: run alembic commands from `backend/` (its `alembic.ini` has
  `script_location` pointing at `../database/migrations`, and `database/migrations/env.py`
  imports the `app` package assuming that working directory).

## Known gaps in this phase

- Docker Compose and the integration test suite were authored and reviewed carefully but
  **not executed** in the environment this phase was built in (no Docker available there).
  Backend unit/component tests (44) were run and pass against SQLite/fakeredis doubles;
  `alembic upgrade head` and the seed script were verified against a real SQLite database.
  Run `docker compose up --build` and the integration suite above before relying on this.
- Playwright e2e spec was authored but not executed for the same reason.
- **No real Oracle instance was available to verify connection testing, capability
  detection, or the collector against.** The connector, edition/version parsing,
  RAC/Statspack detection, privilege-report logic, and session/wait/health sampling in
  `backend/app/oracle/` are covered by tests that drive the real code path against a fake
  connector/connection/cursor mimicking `python-oracledb`'s interface (see
  `backend/tests/test_oracle_capability.py` and `test_collector.py`) - this proves the
  parsing, allowlist enforcement, error-mapping, RBAC, audit-trail, and failure-isolation
  logic are correct, but it cannot catch a real python-oracledb API mismatch, a TNS/network
  quirk, or a V$SESSION/V$SYSTEM_EVENT column-order assumption this platform hasn't seen.
  Run **Test Connection**, **Detect Capabilities**, and let the collector run for a few
  cycles against a real SE2 and a real EE instance before trusting the results. The app
  boots and the scheduler starts/stops cleanly against a local SQLite dev database (checked
  manually) - what's unverified is specifically the live Oracle query path.
- **No real SMTP relay was available to verify email delivery.** The notification pipeline
  (dedup, escalation, recipient resolution, retry/backoff, exhaustion, incident-survives-
  SMTP-failure) is proven against a fake `EmailSender`; the actual `aiosmtplib` call path is
  unexercised. Configure a real SMTP relay under Administration → Notifications and trigger
  a HIGH/CRITICAL incident before trusting real delivery.
- **No local LLM server was available to verify actual model behavior.** The AI gateway,
  tool layer, Copilot, and RCA pipeline are proven end-to-end against a fake `LLMClient` -
  evidence gathering, grounding, JSON-shape parsing (including malformed/markdown-fenced/
  invalid-confidence responses), audit trail, and gateway-failure handling are all real and
  tested. What's unverified: whether a real gpt-oss-120b/20b deployment actually follows the
  RCA JSON-shape instructions reliably, and whether the grounding/prompt-injection rules hold
  against an adversarial prompt embedded in real Oracle session data. Configure `AI_ENABLED`
  and a real local model, then test both the Copilot and RCA against a live incident before
  trusting this in production.
- **Reports**: the SQL Regression and Capacity reports both return real content once a
  database has telemetry of their kind (Enterprise Readiness Plans D.1/D.2 respectively).
  The Health report's resource-limits/operational/backup/Data-Guard/ASM/configuration
  categories are always `UNAVAILABLE` - no collector exists for any of them yet (SQL
  regression and storage are now real, evaluated categories). Reports are persisted indefinitely
  in Postgres (JSON `content` column) - no retention/cleanup policy or admin UI for it exists
  yet (master plan's Administration → Retention is itself unbuilt); report content contains
  no secrets, so indefinite retention carries no credential-exposure risk in the meantime.
  RCA embedded in an incident report (`include_rca: true`) was verified only against a fake
  `LLMClient` - the same "no real local model available" gap as the AI gateway/Copilot/RCA
  phase above. Load/concurrency behavior beyond `asyncio.gather` of a handful of requests in
  the test suite (`backend/tests/test_reports_api.py`) has not been benchmarked against a
  realistic multi-user production load.
- **Fleet management**: tag filtering is a Python-side scan over the fleet's databases, not a
  database-level query - fine at SE2/EE observability scale, but would need revisiting well
  before a fleet reaches thousands of databases. Bulk onboarding has no dry-run/preview mode -
  a submitted batch registers immediately, row by row; there's no "validate first, commit
  second" step. No per-group notification routing or per-group RBAC scoping exists yet - a
  group is purely a filter, not an access boundary (every `database:view` holder sees every
  group). No UI to bulk-assign an existing selection of databases to a group in one action -
  membership is set one database at a time from that database's own drawer.
- **Integrations**: **no real webhook, Slack, or Teams endpoint was available to verify
  actual delivery** - the pipeline (queueing, per-severity dedup, retry/backoff, exhaustion,
  incident-survives-delivery-failure) is proven end-to-end against a fake `WebhookSender`,
  same treatment as email/AI; the real `httpx` call path itself is unexercised. Configure a
  real Slack incoming-webhook URL (or any HTTP endpoint) under Administration → Integrations
  and trigger a HIGH/CRITICAL incident before trusting real delivery. `target_url` validation
  is `http://`/`https://` prefix plus (as of Master Plan Phase 24) the SSRF guard in
  `app/security/ssrf_guard.py`, which rejects a target that resolves to a private/loopback/
  reserved address; an Oracle or SMTP host is still exactly as trusted as before (bounded by
  RBAC, not network-level restriction) since master plan Sec 29's "optional outbound
  restrictions" was scoped to integration targets specifically, the platform's one genuinely
  attacker-influenceable outbound URL. Jira and
  ServiceNow ticket-creation integrations are not implemented - master plan Sec 36 explicitly
  scopes Phase 23 as "Webhooks first, Slack/Teams next," and authenticated issue-tracker APIs
  (different data model - creating/updating a ticket, not posting a notification) are a
  meaningfully larger, separate piece of work than a JSON POST. No delivery-retry backoff has
  been observed against a real flaky endpoint (only the fake sender's deterministic
  failure/success counts).
- **Scale testing**: see [docs/SCALE_TEST_REPORT.md](SCALE_TEST_REPORT.md) in full - the
  short version is that every benchmark ran against SQLite plus an in-process `fakeredis`
  queue in a single process (no real Postgres or Redis server was reachable in this
  environment), so the numbers show this platform's own per-database overhead stays flat
  from 10 to 100 databases, but are not a substitute for re-running the same harness
  (`backend/scripts/scale_bench.py`) against the real production stack with multiple worker
  processes before capacity planning depends on them.
- **DR/recovery** (Master Plan Phase 26): see [docs/DR_RUNBOOK.md](DR_RUNBOOK.md) in full -
  no backup/restore automation exists inside the platform itself (an infrastructure-layer
  responsibility today), and there is no "retry now" action for an exhausted (`FAILED`)
  notification or integration delivery.
- **Adversarial QA** (Master Plan Phase 27): see [docs/THREAT_MODEL.md](THREAT_MODEL.md) Sec
  6 in full - no multi-tenant isolation exists (documented, not fixed - safe only under a
  one-organization-per-deployment model), DNS-rebinding protection is narrowed but not
  eliminated, and every finding was tested statically/at the code level, never against a real
  Oracle/DNS/LLM in the loop.
- **Release candidate** (Master Plan Phase 28): see
  [docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) in full - the real-Postgres/Redis
  integration suite and the Playwright E2E spec both exist and are both fully unexecuted (no
  CI runner, no browser binaries installed); migration reversibility is proven against
  SQLite only, not Postgres.
- **Production certification** (Master Plan Phase 29): see
  [docs/PRODUCTION_CERTIFICATION.md](PRODUCTION_CERTIFICATION.md) in full - **NOT
  CERTIFIED**. Gate G4 (real Oracle connection and telemetry) fails outright: no real Oracle
  instance has ever been connected to this platform. That single failure also fails G11
  (any release blocker is unconditional). Five gates pass, five are partial, one fails - see
  the document for the full gate-by-gate breakdown and the priority-ordered path forward.
- No Activity feed yet - the Overview/Databases/Incidents/AI Copilot/Performance/Sessions/
  Wait Analysis/Blocking & Locks/Health/Reports/SQL Intelligence/Capacity/Change Intelligence
  pages are all real, Statspack integration (Enterprise Readiness Plan D.3) is a real,
  additive panel on the SQL Intelligence page for the databases that have it, and Change
  Intelligence (Enterprise Readiness Plan D.4) is a real, dedicated page; Activity remains a
  placeholder since no collector exists yet for it. See the master plan's Sec 35 phase table
  for the full remaining roadmap (phases 9, 5, 11, and 14 were completed under Enterprise
  Readiness Plan D.1/D.2/D.3/D.4; every phase 0-29 has now been attempted at least once, per
  docs/PRODUCTION_CERTIFICATION.md).
