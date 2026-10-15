# Release Notes — v0.1.0-rc1 (Master Plan Phase 28: Release Candidate)

First release candidate. There is no prior version to diff against, so this document
describes the release as a whole rather than a changelog; see
[docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for whether it is actually safe to
run in production today (short answer: not yet — that document names exactly what's missing).

## What this release is

An Oracle SE2/EE performance observability and AI diagnostics platform, licensing-conscious
by design (no dependency on AWR/ASH/ADDM/Diagnostics Pack for the SE2 monitoring path), with
automated database remediation architecturally absent — this platform monitors, analyzes,
and recommends; it never modifies a monitored database.

## Included (working code, working tests)

- **Platform foundation**: authentication (Argon2id + JWT), RBAC across five roles
  (Platform Admin, DBA, Operator, Viewer, Auditor), secrets encrypted at rest, full audit
  trail, and the frontend application shell.
- **Database onboarding**: connection testing, edition/version/RAC/Statspack-presence
  detection, privilege validation against a fixed licensing-safe allowlist.
- **Telemetry**: a scheduled Oracle collector (sessions, wait events, instance health) with
  per-database freshness/failure tracking, independent of Oracle's own history features.
- **Analytics**: rolling per-hour-of-day baselines (Welford's algorithm) and z-score anomaly
  detection once a metric has enough history.
- **Incidents**: correlation of related anomalies into one incident (not one alert per
  metric sample), severity escalation, full evidence timeline.
- **Alerting**: deduplicated HIGH/CRITICAL email notifications with retry/backoff, delivery
  audit, and (as of this cycle) per-item commit so a mid-batch crash can never resend an
  already-delivered alert.
- **Dashboards**: Overview, Performance, Sessions, Wait Analysis, Blocking & Locks, and
  Health, all against real collected data with honest empty/stale states — no synthetic
  fallback data anywhere in this platform.
- **AI Copilot & RCA**: grounded natural-language Q&A and structured root-cause analysis
  over this platform's own telemetry, through a fixed read-only tool layer with no path to
  an Oracle credential, an Oracle connection, or arbitrary SQL.
- **SQL Intelligence** (master plan Phase 9, Enterprise Readiness Plan D.1): top SQL ranked
  by real per-interval delta, plan history captured on change, and deterministic regression
  detection (plan change or elapsed-per-execution degradation against a statement's own
  recent history) — its own collector, on its own interval, failure-isolated from the main
  session/wait/health collector. Grounds three new AI tools and a real SQL Intelligence page.
- **Capacity/Tablespace Intelligence** (master plan Phase 5, Enterprise Readiness Plan D.2):
  per-tablespace %used from a single collection round, a deterministic linear growth-rate fit
  projecting time-to-full, and independent, non-suppressing `PCT_USED`/`PROJECTED_FULL`
  alerts against operator-configurable thresholds — its own collector, on its own (much
  slower) interval, failure-isolated from the other collectors. Grounds a new AI tool and a
  real Capacity page.
- **Statspack integration** (master plan Phase 11, Enterprise Readiness Plan D.3): for
  databases that already have Statspack installed and snapshotting, an incremental sync of
  new snapshots into this platform's own tables, and top-wait-class/top-SQL deltas between
  the two most recently synced snapshots — reuses D.1's own delta-computation logic
  unmodified. Grounds a new, on-demand AI tool and an additive "Statspack" panel that
  appears only for databases that have it.
- **Change Intelligence** (master plan Phase 14, Enterprise Readiness Plan D.4): configuration
  parameter drift detection from `V$SYSTEM_PARAMETER` — periodic snapshots of every
  explicitly-tuned (non-default) parameter, diffed between the two most recent collection
  rounds to surface parameters newly set, changed, or reverted to default. Grounds a new,
  on-demand AI tool, a de-stubbed `"configuration"` health-check category, and a real
  dedicated "Change Intelligence" page.
- **Reports**: six report types (Daily DBA, Incident, Performance, SQL Regression, Capacity,
  Health) with CSV/PDF export; every section is real once its own telemetry exists, and
  honestly reports itself unavailable rather than fabricated until then.
- **Fleet management**: named database groups, freeform tags, bulk onboarding (up to 100 at
  once, one row's failure never aborts the batch).
- **Integrations**: outbound webhook/Slack/Teams delivery on the same HIGH/CRITICAL
  threshold and dedup rule as email, with its own retry/backoff outbox.
- **Security hardening**: per-IP and per-route rate limiting, request body-size limits,
  standard security response headers, an SSRF guard on integration targets (checked at both
  configuration time and immediately before every send).
- **Scale-tested**: collector/analytics overhead confirmed flat from 10 to 100 databases;
  a silent fleet-truncation bug past 1,000 databases was found and fixed.
- **DR/recovery-proven**: Oracle-unreachable, collector-restart, and Postgres/Redis-outage
  scenarios all recover automatically; a duplicate-alert bug under a mid-batch process crash
  was found and fixed.
- **Adversarially tested**: SQL injection, RBAC bypass, credential leakage, AI prompt
  injection, and SSRF bypass all audited; two real findings (a credential leak in validation
  errors, an SSRF timing gap) fixed.
- **RCA quality benchmark suite** (master plan Phase 20, Enterprise Readiness Plan E.1): a
  5-scenario gold-incident set scored by 5 deterministic hallucination/uncertainty checks, run
  for real against a real local model (Ollama + `qwen2.5:7b-instruct`) — the real result is on
  record, not glossed over: zero hallucinated identifiers and zero unsupported-confidence
  claims, but the model declined to name a root cause on 4 of 5 scenarios even where the
  evidence clearly supported one. See [docs/RCA_BENCHMARK_REPORT.md](RCA_BENCHMARK_REPORT.md).

## Explicitly not included

- **Distinct named AI agents** (Performance/SQL/Wait/Lock/Capacity/Change agents per master
  plan Sec 22) — a single unified, deterministic evidence-gathering tool layer exists instead.
- **Jira/ServiceNow integrations** — webhooks/Slack/Teams only, per the master plan's own
  phased prioritization ("Webhooks first, Slack/Teams next").
- **Multi-tenant isolation** — every authorization check is resource-class scoped, not
  per-tenant. Safe under a one-organization-per-deployment model; not safe if this platform
  is ever deployed to serve multiple organizations from one shared instance. See
  [docs/THREAT_MODEL.md](THREAT_MODEL.md) Sec 1/4.2.

## Known limitations carried into this release

Every phase from Identity+RBAC onward that touches Oracle, SMTP, a local LLM, Postgres, or
Redis was built and tested against this platform's own code paths using faithful test
doubles — this development environment has never had a real Oracle instance, SMTP relay,
LLM server, Postgres server, or Redis server reachable. A real-infrastructure integration
suite exists (`tests/integration/`, 4 tests) and is wired into CI (`.github/workflows/ci.yml`)
to run against real Postgres + Redis on every push/PR — **it has never actually executed**,
because this repository has no connected git remote or CI runner yet. See
[docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for the full, current gap list.

## Versioning and packaging

This release is identified as `v0.1.0-rc1` (matching the `0.1.0` version already set in
`backend/app/main.py` and `frontend/package.json`). No container registry or image-publishing
pipeline exists yet — `docker-compose.yml` builds both services from source, matching the
master plan's own "Phase A: Docker Compose single-node" deployment strategy (Sec 31); a
versioned-image pipeline is Phase B/C territory. Software bills of materials for both
services are published alongside this release:
[docs/sbom-backend.json](sbom-backend.json) (54 components, CycloneDX 1.4) and
[docs/sbom-frontend.json](sbom-frontend.json) (340 components, CycloneDX 1.6) — both
generated from the actual locked dependency sets (`requirements.txt`,
`package-lock.json`), not hand-maintained.
