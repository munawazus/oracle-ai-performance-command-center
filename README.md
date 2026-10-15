# Oracle AI Performance Command Center

Licensing-conscious Oracle database performance observability and AI diagnostics platform,
with Standard Edition 2 as a first-class target. See the Phase 0/1 discovery baseline for
product scope, target architecture, and the full phase roadmap.

This repository currently implements (the "Phase N" labels below are this repo's own
implementation-history numbering, not the master plan's - see the Master Plan Phase 21
entry for what explicit master-plan-phase mapping looks like going forward):

- **Phase 0/1: platform foundation and secure application shell** - authentication, RBAC,
  secrets management, audit logging, database registration (metadata + encrypted
  credentials), and the frontend application shell.
- **Phase 2: database capability detection** - a real connection test against Oracle
  (`python-oracledb`), version/edition/RAC/Statspack detection, and privilege validation
  against a fixed, licensing-safe base-view allowlist (never AWR/ASH/ADDM/Diagnostics or
  Tuning Pack views).
- **Phase 3: Oracle collectors and historical storage** - a scheduled collector
  (APScheduler) samples V$SESSION/V$SYSTEM_EVENT/V$INSTANCE for every active database on a
  configurable interval and writes to this platform's own PostgreSQL history (never called
  ASH), with per-database collector status (last run, consecutive failures) surfaced on the
  Overview page.
- **Phase 4: deterministic analytics and baselining** - a second scheduled job turns raw
  telemetry into current metric values (active session count, per-wait-class wait-time
  rate), folds each into a rolling per-hour-of-day baseline (Welford's algorithm), and
  flags a z-score anomaly once a metric has enough history to judge against
  (`GET /api/databases/{id}/anomalies`).
- **Phase 5: incident correlation** - anomalies for the same database within a 30-minute
  window attach as evidence to one incident instead of creating a duplicate (master plan
  Sec 7's "don't create one incident per alert" requirement), with severity escalation,
  a real timeline, and acknowledge/assign/resolve actions (`incident:manage`) - a full
  **Incidents** page replaces that nav item's placeholder.
- **Phase 6: email alerting** - the moment an incident first reaches HIGH or CRITICAL, a
  deduplicated notification event is queued (never repeated for the same incident at the
  same severity) and an async processor sends it over SMTP with retry/backoff up to 5
  attempts. Delivery status (`PENDING`/`RETRYING`/`SENT`/`FAILED`) is visible per incident
  and the incident itself is never affected by SMTP failing. SMTP/recipients are
  configurable from **Administration → Notifications** - no credentials in the email.
- **Phase 8: AI gateway and agents** - a model-agnostic gateway (any OpenAI-compatible local
  server - vLLM/Ollama/llama.cpp - targeting gpt-oss-120b with a gpt-oss-20b fallback) behind
  a fixed, read-only tool layer that only ever reads this platform's own Postgres data -
  never an Oracle connection, never a credential, never arbitrary SQL. A real **AI Copilot**
  page answers grounded questions about a database, and an **Investigate with AI** action on
  each incident produces a structured root-cause analysis (probable cause, confidence,
  evidence, alternatives, recommended investigation/action, and an explicit data-completeness
  note) - never fabricating certainty when the evidence doesn't support one. Off by default
  (`AI_ENABLED=false`) since no local model is assumed to exist.
- **RCA quality benchmark suite** (master plan Phase 20, Enterprise Readiness Plan E.1) - a
  5-scenario gold-incident set (`app/rca_benchmark/gold_incidents.py`) scored by 5
  deterministic checks (`app/rca_benchmark/scoring.py`: schema adherence, confidence
  consistency, no fabricated identifiers, uncertainty calibration on sparse evidence, and
  engagement with clear evidence) - never a second LLM judging the first. Run for real
  (`backend/scripts/rca_benchmark.py`, never part of CI) against a real local model (Ollama +
  `qwen2.5:7b-instruct`): zero hallucinated identifiers and zero unsupported-confidence claims,
  but the model declined to name a root cause on 4 of 5 scenarios even where the evidence
  clearly supported one - a real, honestly-reported finding, not a clean pass. See
  [docs/RCA_BENCHMARK_REPORT.md](docs/RCA_BENCHMARK_REPORT.md).
- **Phase 10: dashboard suite** - five real pages replace their Sidebar placeholders:
  **Performance** (active-session-count timeline over a 15m/1h/6h/24h/7d range, with
  anomalies in that window listed alongside it), **Sessions** (a live V$SESSION-derived
  table per database, with a blocked-by indicator), **Wait Analysis** (per-wait-class
  centisec/s rates, computed as a delta between the two most recent collection rounds),
  **Blocking & Locks** (blocking chains, flagging a blocker no longer present in the latest
  sample rather than guessing its current state), and **Health** (fleet-wide collector and
  capability-probe status across every registered database, distinct from the per-database
  Capabilities tab on the Databases page). `app/telemetry_views.py` extracts the
  latest-round/blocking-chain logic shared between the AI tool layer (Phase 8) and these new
  REST endpoints, so the two never drift. Every page shows an honest "not enough data yet" or
  "no databases registered" state instead of a fabricated chart - there is no synthetic
  fallback data anywhere in this platform. Activity remains a placeholder - no collector
  exists yet for it. SQL Intelligence (master plan Phase 9, Enterprise Readiness Plan D.1),
  Capacity (master plan Phase 5, Enterprise Readiness Plan D.2), and Change Intelligence
  (master plan Phase 14, Enterprise Readiness Plan D.4) are all real pages now, and Statspack
  integration (master plan Phase 11, Enterprise Readiness Plan D.3) is a real, additive panel
  on the SQL Intelligence page - see below.
- **SQL Intelligence** (master plan Phase 9, Enterprise Readiness Plan D.1) - a dedicated
  collector (`app/oracle/sql_probe.py`) samples `V$SQL`/`V$SQL_PLAN` on its own interval
  (`SQL_COLLECTOR_INTERVAL_SECONDS`, 300s by default), independent of and failure-isolated
  from the main 15s session/wait/health collector. Top SQL is ranked by real per-interval
  delta between the two most recent rounds (never a live query on read); plan history is
  captured on change (one row per distinct plan hash, never re-snapshotted while unchanged);
  a deterministic comparator (`app/analytics/sql_regression.py`) flags a plan change or an
  elapsed-per-execution regression against a statement's own recent history - not a
  statistical anomaly model. Exposed via `GET /api/databases/{id}/top-sql`,
  `.../sql/{sql_id}`, `.../sql/{sql_id}/plan/{hash}`, `.../sql-regressions`, three AI tools
  (`get_top_sql`, `get_sql_plan`, `get_sql_regressions`), and the **SQL Intelligence** page.
- **Capacity/Tablespace Intelligence** (master plan Phase 5, Enterprise Readiness Plan D.2) -
  a dedicated collector (`app/oracle/tablespace_probe.py`) samples `DBA_TABLESPACES`/
  `DBA_DATA_FILES`/`DBA_FREE_SPACE`/`DBA_TEMP_FILES`/`V$TEMP_SPACE_HEADER` on its own, much
  slower interval (`CAPACITY_COLLECTOR_INTERVAL_SECONDS`, 1h by default), independent of and
  failure-isolated from the other collectors. Unlike SQL's per-interval delta, current %used
  is available from a single collection round; a deterministic linear growth-rate fit over
  the trailing 7 days (`app/analytics/capacity_metrics.py`) projects time-to-full, and a
  comparator (`app/analytics/capacity_alerts.py`) raises independent, non-suppressing
  `PCT_USED`/`PROJECTED_FULL` alerts against operator-configurable thresholds. Exposed via
  `GET /api/databases/{id}/capacity`, `.../capacity/{tablespace_name}`,
  `.../capacity-alerts`, the `get_tablespace_trend` AI tool, and the **Capacity** page.
- **Statspack integration** (master plan Phase 11, Enterprise Readiness Plan D.3) - for
  databases that already have Statspack installed and actively snapshotting (this platform
  never creates snapshots, only detects and reads them), an incremental sync of new
  `PERFSTAT` snapshots into this platform's own tables, and top-wait-class/top-SQL deltas
  between the two most recently synced snapshots, reusing D.1's own delta-computation logic
  unmodified. Exposed via `GET /api/databases/{id}/statspack`, the on-demand
  `get_statspack_summary` AI tool, and an additive "Statspack" panel on the **SQL
  Intelligence** page that appears only for databases that have it.
- **Change Intelligence** (master plan Phase 14, Enterprise Readiness Plan D.4) - a dedicated
  collector (`app/oracle/config_probe.py`) samples `V$SYSTEM_PARAMETER` (only explicitly-
  tuned, non-default parameters) on its own interval (`CONFIG_COLLECTOR_INTERVAL_SECONDS`,
  1h by default), independent of and failure-isolated from the other collectors - the
  simplest of the four, since it's a single query needing no capability gate or
  partial-capability handling. A set-diff (`app/analytics/config_metrics.py`, the one place
  this platform's own delta machinery from D.1 didn't fit - a configuration round is a set of
  name/value pairs, not a numeric rate over a shared counter) between the two most recent
  rounds classifies each parameter as newly set, changed, or reverted to default. Exposed via
  `GET /api/databases/{id}/configuration`, the on-demand `get_change_history` AI tool, the
  now-real `"configuration"` health-check category, and the **Change Intelligence** page.
- **Master Plan Phase 21: Reports** - `POST /api/reports` generates one of six report types
  (Daily DBA, Incident, Performance, SQL Regression, Capacity, Health) from this platform's
  own persisted telemetry/incident/anomaly/notification data, persists it (`reports` table),
  and audits generation (`report.generated`) and every export (`report.exported`). A real
  **Reports** page replaces its placeholder: generate (with database/incident/period
  filters), a filterable list, a view drawer with the full content plus its
  `data_completeness_note`/`limitations`, and CSV/PDF download - both formats stream straight
  into the HTTP response (never a filesystem path, so there is no export-side path-traversal
  surface). The SQL Regression report returns real top-SQL and open-regression content once a
  database has any SQL telemetry, and the Capacity report returns real per-tablespace usage/
  growth/alert content once a database has any capacity telemetry (both `data_available:
  false` only until then - a data-availability fact, not a feature-doesn't-exist fact).
  The Health report runs a deterministic 11-category health-check engine
  (`app/reports/health_check.py`) where every category the master plan names is always
  present in the output - a category with no collector (resource limits, operational,
  backup, Data Guard, ASM) is reported `UNAVAILABLE`, never silently passed; SQL regression
  and storage are real, evaluated PASS/WARN/FAIL categories once telemetry exists, and
  configuration (Enterprise Readiness Plan D.4) is a real PASS/WARN category (no FAIL tier -
  a configuration change isn't inherently a failure severity) - all three UNAVAILABLE only
  until their own telemetry exists.
- **Master Plan Phase 22: Fleet Management** - named **DB groups** (many-to-many, a database
  can belong to any number) and freeform, normalized **tags** on every `MonitoredDatabase`,
  both usable as fleet filters on the Databases list, the Fleet Overview, and the global
  Incidents view (`?group_id=`/`?tag=` on `GET /api/databases` and `GET /api/incidents`).
  **Bulk onboarding**: `POST /api/databases/bulk` registers up to 100 databases in one call
  through the exact same validation/secret-storage/audit path as a single registration - one
  row's failure (e.g. a duplicate name) never aborts the rest of the batch, and every row's
  outcome is reported individually. The Overview page's per-database card now shows its open-
  incident count and links straight into the Incidents view pre-filtered to that database -
  the "incident drill-down" the master plan's Fleet Overview screen calls for. Fixed a real,
  previously-invisible bug along the way: SQLite was never enforcing foreign keys (including
  `ON DELETE CASCADE`), so `DELETE /api/databases/{id}` would have failed under real
  constraint enforcement (as Postgres always applies) the moment a credential/secret pair
  existed - see docs/RUNBOOK.md.
- **Master Plan Phase 23: Integrations** - configurable outbound **webhook/Slack/Teams**
  channels ("Webhooks first, Slack/Teams next" per master plan Sec 36), any number of them at
  once, each firing on the same HIGH/CRITICAL threshold and per-severity dedup rule as email.
  `app/integrations/payloads.py` renders the right shape per channel (a generic JSON payload,
  a Slack incoming-webhook block, or a Teams MessageCard) once at queue time; delivery goes
  through its own async outbox (`integration_deliveries`) with the identical create → queue →
  retry (`[60, 300, 900, 3600]`s backoff, 5 attempts) → record shape as email notifications,
  on its own scheduler job. A generic `WEBHOOK` target can carry an optional HMAC-SHA256
  signing secret (`X-Signature-256` header) so a receiver can verify authenticity. A real
  **Test** button fires one synchronous delivery and reports success/failure in the response
  body - never a 500 for an unreachable endpoint, same contract as Test Connection. New
  **Integrations** tab under Administration (list/create/edit/delete/test/enable-disable +
  per-integration delivery history), and each incident's drawer shows integration delivery
  status alongside its existing email status. Jira/ServiceNow ticket creation is explicitly
  out of scope for this pass - see docs/RUNBOOK.md's Known Gaps.
- **Master Plan Phase 24: Security Hardening** - a blanket per-IP rate-limit floor
  (`RateLimitMiddleware`, 300/min by default) on every API request, tighter per-authenticated-
  user limits on the platform's most expensive operations (AI Copilot/RCA, report generation,
  bulk onboarding, integration test-delivery), a request body-size ceiling checked before any
  parsing, and standard security response headers (`X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security` in
  production) on every response. A new SSRF guard (`app/security/ssrf_guard.py`) blocks an
  integration's `target_url` from resolving to a private/loopback/link-local/reserved address
  at configuration time - checked via a mockable resolver Protocol, same test-double pattern
  as every other external dependency in this codebase, so tests never make a real DNS/socket
  call. Login's existing rate limit and the new per-route limits now correctly return 429 with
  a `Retry-After` header (previously login returned a plain 401). New
  [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) documents trust boundaries, attacker classes,
  and the full control inventory. This phase closed gaps and added controls - it explicitly
  did not run a red-team/adversarial pass (master plan Phase 27, a separate later phase).
- **Master Plan Phase 25: Scale Testing** - a benchmark harness
  (`backend/scripts/scale_bench.py`) exercises the collector, analytics engine, fleet-list/
  incident-list API reads, notification/integration queues, and AI Copilot orchestration at
  the master plan's own 10/50/100-database tiers plus a 500-item queue backlog and up to 50
  concurrent AI requests. Found and fixed a real bug along the way: the collector and
  analytics schedulers would have silently stopped polling any database past the 1000th in
  a fleet (`MonitoredDatabaseRepository.list_active_ids()` now pages internally with no cap).
  Confirmed a second, smaller scale ceiling that was *not* fixed this phase and is tracked as
  a known gap: `GET /api/databases` truncates past 200 databases (fine at every tested
  10/50/100 tier, but needs pagination before a fleet can safely exceed 200). Full
  methodology, results, and honest gaps - including that this environment has no reachable
  Postgres or Redis server, so every number reflects SQLite + an in-process fake queue, not
  the production stack - are in
  [docs/SCALE_TEST_REPORT.md](docs/SCALE_TEST_REPORT.md).
- **Master Plan Phase 26: DR/Recovery** - proves this platform's own five named recovery
  scenarios (Oracle unreachable, collector/API process restart, PostgreSQL outage, queue
  recovery, notification provider outage) either recover fully automatically or document the
  manual infrastructure-layer procedure an operator runs - see
  [docs/DR_RUNBOOK.md](docs/DR_RUNBOOK.md). Found and fixed a second real bug: the
  notification and integration delivery queues used to commit their whole batch (up to 50
  items) in one transaction, so a process killed mid-batch - after some emails/webhooks had
  already gone out over real SMTP/HTTP but before the commit recording them as sent - would
  resend those same alerts on the very next tick. Both queues now commit after every
  individual item, so a mid-batch crash can only cost the one in-flight item's own progress,
  never redo already-completed sends. `GET /api/ready` was also proven to both report and
  recover from a simulated Postgres or Redis outage with no app restart required.
- **Master Plan Phase 27: Adversarial QA** - five parallel audits (SQL injection, RBAC
  bypass, credential leakage, AI prompt injection, SSRF bypass) actively tried to break the
  controls built in earlier phases rather than just confirm their happy path. SQL injection:
  every raw-SQL construction site in the codebase (there is exactly one, a bandit-flagged
  `# nosec B608`) confirmed safe-by-design, with a new regression test proving the allowlist
  gate around it actually rejects a malicious view name. Credential leakage: found and fixed
  a real bug - `RequestValidationError`'s handler echoed a field's raw submitted value back
  in 422 responses, which for a password/secret field that failed validation (too long, wrong
  type) meant the credential itself was reflected in the error body. SSRF: adversarially
  tested six specific bypass techniques against the Phase 24 guard (DNS rebinding, HTTP
  redirects, IPv6 encodings, alternate IP literal encodings, multi-address DNS responses, the
  dev-only escape hatch) - five were already blocked; added a second SSRF check that fires
  immediately before every webhook send (not just at integration create/update), narrowing
  the one confirmed residual gap (DNS rebinding). RBAC: audited all 47 routes, found none
  missing authorization - but surfaced and documented an unexamined assumption: this platform
  has no multi-tenant isolation (the master plan's own `organizations/tenants` data-model
  entity was never implemented), so every authorization check is resource-class scoped, safe
  only under a one-deployment-per-organization model. AI prompt injection: confirmed the
  existing grounding-rules framing (`app/ai/prompts.py`) genuinely labels Oracle-sourced
  evidence as untrusted data, but was untested against an actual adversarial payload - added
  a regression test that plants an injected instruction into a session's module/program field
  and proves the framing text survives and the payload stays confined to the labeled evidence
  block, never the system prompt. Full findings in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
- **Master Plan Phase 28: Release Candidate** - packages and documents this release as
  `v0.1.0-rc1`: [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md) (what's in and explicitly not
  in this release), [docs/UPGRADE_AND_ROLLBACK.md](docs/UPGRADE_AND_ROLLBACK.md) (the
  procedure for future releases, plus proof this cycle that the full 9-migration chain
  upgrades and downgrades cleanly end-to-end against SQLite - not yet proven against
  Postgres), and real CycloneDX software bills of materials for both services
  ([docs/sbom-backend.json](docs/sbom-backend.json), 54 components;
  [docs/sbom-frontend.json](docs/sbom-frontend.json), 340 components). The master plan's own
  Sec 52 "final declaration" - exact test suites, pass/fail counts, known limitations, open
  risks, supported Oracle versions, infrastructure requirements, and specific evidence for
  email alerting/AI tool safety/UI control coverage - is
  [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md), which explicitly declines to
  declare this production-ready: a real-Postgres/Redis integration suite (`tests/integration/`,
  wired into CI) and a Playwright E2E spec both exist, fully coded, and have never executed
  even once - this repository has no connected git remote or CI runner, and no real Postgres/
  Redis/browser infrastructure exists in any environment this project has been built in.
- **Master Plan Phase 29: Production Certification** - the master plan's own final sign-off
  gate (Sec 36/37: eleven release gates, G0-G11), evaluated against actual accumulated
  evidence rather than test-count momentum. **Decision: NOT CERTIFIED.** 5 of 11 gates pass
  outright, 5 are partial, and one fails: G4 ("real Oracle connection and telemetry") - no
  real Oracle instance has ever been connected to this platform, at any point in its
  history, which by itself also fails G11 ("any release blocker" is unconditional). Nothing
  on the resulting punch list requires new code - it requires running already-built code
  (a real Oracle connection, the already-CI-wired real-Postgres/Redis integration suite, a
  real SMTP relay, the Playwright E2E spec, a real local LLM) against infrastructure this
  development environment has never had. Full gate-by-gate reasoning and the priority-ordered
  path to certification are in
  [docs/PRODUCTION_CERTIFICATION.md](docs/PRODUCTION_CERTIFICATION.md).

No Oracle instance was available to verify Phases 2-3 against live during development, no
real SMTP relay was available to verify Phase 6's actual delivery path, no local LLM server
was available to verify Phase 8's actual model responses, and no real webhook/Slack/Teams
endpoint was available to verify Phase 23's actual delivery path - see docs/RUNBOOK.md's
Known Gaps. Phases 4-5, 10, 21, 22, and 24 have no such gap - they only read/write this
platform's own Postgres and Redis, with no external dependency of their own. Phase 25's own
benchmark had no real Postgres/Redis to run against either - see docs/SCALE_TEST_REPORT.md.
Phase 26 has the same gap again, for the same reason: no real Postgres, Redis, Oracle, or
SMTP outage could be induced in this environment - every recovery scenario is proven against
a simulated failure of this platform's own code, not a real infrastructure outage - see
docs/DR_RUNBOOK.md. Phase 27's adversarial testing was static/code-level for the same reason
- no real Oracle instance to inject a live prompt-injection payload through, no real DNS
server to attempt an actual rebinding attack against, no real LLM to test the grounding
framing against - see docs/THREAT_MODEL.md's Sec 6.

The remaining master-plan phase (an Activity feed) is next - see
[docs/RUNBOOK.md](docs/RUNBOOK.md) and the master plan for what's built vs. planned.

## Quickstart

```bash
cp .env.example .env          # then edit the generated secrets - see below
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`, health at `/api/health`, readiness at `/api/ready`)
- Frontend: http://localhost:5173
- On first boot, the backend container runs migrations, seeds roles/permissions, and (if
  `ADMIN_BOOTSTRAP_EMAIL`/`ADMIN_BOOTSTRAP_PASSWORD` are set in `.env`) creates the first
  ADMIN account.

Generate real secrets before running anything beyond a throwaway local instance:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"        # JWT_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SECRET_ENCRYPTION_KEY
```

Full setup, testing, and troubleshooting instructions: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Repository layout

```text
backend/      FastAPI application, SQLAlchemy models, services, tests
database/     Alembic migrations (versioned schema, applied via backend/alembic.ini)
frontend/     React + TypeScript + Vite application, component tests, Playwright e2e
tests/        Cross-cutting integration tests requiring real Postgres/Redis
docs/         Runbook and architecture notes
deployment/   nginx config and other deployment-time assets
scripts/      Local dev convenience scripts
```

## Product scope and constraints

Not a reimplementation of OEM/AWR/ASH/ADDM for Oracle Standard Edition 2 - those sit behind
the Diagnostics and Tuning Packs, which SE2 cannot license. Telemetry and history are built
on base V$ views and this platform's own PostgreSQL repository instead. This product
monitors, analyzes, and recommends - it does not perform automated database remediation.
