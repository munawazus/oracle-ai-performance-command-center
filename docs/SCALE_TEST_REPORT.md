# Scale Test Report — Master Plan Phase 25

Master plan deliverable: "Prove performance under fleet load" — 10/50/100+ database
scenarios, storage benchmarks, collector overhead, queue saturation, AI concurrency.

## Methodology and environment (read this before the numbers)

This sandboxed development environment has **no reachable Postgres server and no reachable
Redis server** (both time out on connect — verified directly before writing this benchmark).
Production (`docker-compose.yml`) runs Postgres 16 and Redis; local dev (`backend/.env`)
runs SQLite. Every number in this report was produced by
[`backend/scripts/scale_bench.py`](../backend/scripts/scale_bench.py) against
**SQLite-on-local-disk plus an in-process `fakeredis` queue**, in a single Python process —
not the production stack, and not multiple worker processes contending for the same
database/queue.

What that does and does not tell us:

- **Representative regardless of backend**: this platform's own per-database Python
  overhead (ORM construction, normalization, correlation/baseline math, Pydantic
  serialization) and whether any code path has an accidental unbounded-result-set or
  O(n²) shape that a faster database would not fix. The Phase 25 finding below (a silent
  fleet-size cap in the collector/analytics schedulers) is exactly that kind of finding,
  and it was real regardless of which database backend runs underneath.
- **Not representative**: absolute latency numbers, and anything about Postgres/Redis
  behavior under concurrent connections from multiple worker processes. A future run of
  this same script against a real `docker compose up` stack would produce different
  (very likely different but not necessarily worse) absolute numbers; the harness is
  written so that only `DATABASE_URL`/`REDIS_URL` need to change to do that.
- No real Oracle instance, real SMTP relay, or real local LLM server exists in this
  environment either (a standing gap across every phase — see `docs/RUNBOOK.md`), so
  collector/notification/AI benchmarks all measure this platform's own orchestration
  overhead around instant fake test doubles, never real network I/O to those systems.

Run it yourself: `cd backend && python scripts/scale_bench.py` (takes under a minute; writes
`docs/scale_bench_results.json`, the raw data this report is drawn from).

## 1. Collector and analytics overhead per database

Fleet sizes 10 / 50 / 100 (per master plan Phase 25's own tiers), each seeded with multiple
collection rounds (20 / 8 / 4 respectively — fewer rounds at larger fleet sizes purely to
keep this sweep's own runtime bounded; round count does not change per-call overhead, only
how many samples were averaged).

| Fleet size | Rounds | Collector p50 / p95 (ms/db) | Analytics p50 / p95 (ms/db) |
|---|---|---|---|
| 10  | 20 | 5.00 / 6.47 | 5.01 / 6.32 |
| 50  | 8  | 4.53 / 5.92 | 4.56 / 5.85 |
| 100 | 4  | 4.55 / 6.09 | 4.52 / 6.08 |

**Finding: flat, not growing with fleet size.** Per-database collector/analytics overhead
stays in the same ~4.5–5ms band from 10 to 100 databases — each database's collection cycle
is independent (its own DB session, per `app/services/collector_service.py`'s own
docstring), so there is no shared-state cost that grows with fleet size. At the real
production `COLLECTOR_INTERVAL_SECONDS` default of 15s, a 100-database fleet's full collector
pass here took well under 1 second of actual work (100 × ~4.6ms ≈ 460ms), leaving ample
headroom before the next interval — though this excludes real Oracle network round-trip
time, which this environment cannot measure (see Methodology).

## 2. API read latency (fleet overview / incident list)

`GET /api/databases` and `GET /api/incidents`, 20 repeated calls per fleet size, full HTTP
stack (auth, RBAC, rate-limit/body-size/security-header middleware, Pydantic serialization)
via an in-process ASGI client — no real network hop, so these are a floor, not a ceiling, on
real deployed latency.

| Fleet size | GET /api/databases p50 / p95 | GET /api/incidents p50 / p95 |
|---|---|---|
| 10  | 19.02ms / 50.75ms | 18.83ms / 22.92ms |
| 50  | 16.15ms / 19.38ms | 15.39ms / 16.69ms |
| 100 | 18.47ms / 21.19ms | 15.78ms / 17.86ms |

All comfortably inside the master plan's own SLO target (Section 54: "Core dashboard API
p95 < 1.5s"), by roughly two orders of magnitude, at every tested fleet size. The fleet-10
p95 outlier (50.75ms vs. a 19.02ms median) is Python/event-loop warmup on the first few calls
of a freshly reset process, not a fleet-size effect — it does not recur at fleet 50 or 100
despite those doing more total work per call.

**Finding: `GET /api/databases` silently truncates past 200 databases.**
`DatabaseService.list_databases()` calls the repository's `list_all()`, whose default page
size is 200 rows, with no pagination in the API or the frontend Databases/Fleet Overview
page. `app/services/database_service.py` already carries a comment acknowledging this
assumption ("Fleet sizes here are small enough... that this is not a real cost"). This
benchmark confirms the assumption holds at the master plan's explicit 10/50/100 tiers
(`returned_by_list_databases` exactly matched `fleet_size` at all three), but a fleet past
200 databases would see the Databases list and Fleet Overview silently stop at the 200th
database (alphabetically) with no error or "showing 200 of N" indicator. This is a real
scale ceiling, tracked as a known gap below rather than fixed in this phase — fixing it
properly means adding pagination to the API response shape and the frontend list/table,
which is frontend feature work disproportionate to a testing/benchmarking phase; it does not
block the master plan's own 10/50/100+ scale target, which this fleet-listing endpoint
handles correctly.

## 3. Fix shipped this phase: collector/analytics silent truncation past 1000 databases

While researching this benchmark, both `CollectorService.run_once_all_active()` and
`AnalyticsService.run_once_all_active()` (the two scheduler jobs that decide which databases
actually get polled every interval) were found calling `list_all(limit=1000)` — a fleet
larger than 1000 databases would have silently stopped receiving telemetry and analytics for
every database past the 1000th, with no error, no log line, and no UI indicator. Unlike the
`GET /api/databases` display cap above, this is a correctness/production-safety issue (missed
monitoring, not just a truncated list view), so it was fixed rather than only documented:
added `MonitoredDatabaseRepository.list_active_ids()` ([`database_repo.py`](../backend/app/repositories/database_repo.py)),
which pages internally in fixed 500-row batches with no upper bound, and switched both
schedulers to use it. Regression-tested at 650 active databases (past the first 500-row
internal page) in
[`test_scale_hardening.py::test_list_active_ids_covers_a_fleet_larger_than_one_internal_page`](../backend/tests/test_scale_hardening.py).

## 4. Storage growth

| Fleet size | Rounds seeded | Session/wait/health rows (approx) | SQLite file size | Bytes/database |
|---|---|---|---|---|
| 10  | 20 | 200 | 393,216 B (384 KB) | 39,321.6 B |
| 50  | 8  | 400 | 405,504 B (396 KB) | 8,110.1 B |
| 100 | 4  | 400 | 421,888 B (412 KB) | 4,218.9 B |

Per-database storage cost falls as fleet size grows in this data because SQLite allocates
in fixed page-size increments (the near-flat 384–412 KB file sizes across very different
data volumes are dominated by schema/index/journal overhead at this small a scale, not
per-row cost) — this table should be read as "total file size stayed under 500 KB for every
tested scenario," not as a per-database unit-cost curve; a meaningful bytes-per-database-
per-day figure needs either a much larger seeded history (thousands of rounds) or a real
Postgres instance where table/index storage accounting is exposed directly, neither of which
this environment can produce (see Methodology). This is an honest gap, not a result: **actual
production storage growth was not benchmarked at meaningful scale** and should be re-run
against a real Postgres instance before capacity planning depends on it.

## 5. Queue saturation

Seeded 500 pending `NotificationEvent` rows (one incident each) against a single database,
then ran `NotificationService.process_pending()` repeatedly — the same bounded-batch-per-tick
design as production (`app/services/notification_service.py` caps itself at 50 events per
call, matching the scheduler's own per-tick budget).

| Metric | Result |
|---|---|
| Backlog seeded | 500 |
| Batch size per tick | 50 |
| Ticks to fully drain | 10 (= `ceil(500/50)`, exact) |
| Items drained | 500 / 500 |
| Total wall-clock | 1.356s |
| Throughput | 368.8 items/sec |

The queue drained in exactly the expected number of ticks with no items lost or
double-processed. At the production `NOTIFICATION_INTERVAL_SECONDS` default of 10s, a
500-item backlog (500 simultaneous HIGH/CRITICAL incidents — a scenario that would itself
indicate a severe fleet-wide outage) would fully drain in 10 ticks × 10s = 100 seconds,
comfortably inside "incident still gets emailed eventually" territory. The same
bounded-batch-draining property is independently confirmed for the Phase 23 integration
(webhook/Slack/Teams) outbox in
[`test_scale_hardening.py::test_integration_queue_drains_a_backlog_larger_than_one_batch`](../backend/tests/test_scale_hardening.py).
This benchmark used `fakeredis`, not real Redis (see Methodology) — the queue's own
correctness (bounded batches, no double-send) is proven either way since the queue logic
lives entirely in Postgres/SQLite rows, not in Redis; only the per-route AI/bulk-operation
rate limiters (Phase 24) actually depend on Redis, and those are exercised under real
concurrency separately in Section 6.

## 6. AI Copilot concurrency

`CopilotService.ask()` fired concurrently via `asyncio.gather`, against a fake LLM client
with an injected 150ms delay (simulating a local model's real response time — no real
gpt-oss-120b server exists in this environment to measure directly). This isolates the
orchestration layer's own concurrency behavior (evidence gathering, tool calls, audit
writes) from model latency, which this environment cannot produce.

| Concurrency | Total wall-clock | Serial lower bound (N × 150ms) | Speedup factor |
|---|---|---|---|
| 1  | 0.190s | 0.150s | 0.79× (single-request overhead, not a regression) |
| 10 | 0.483s | 1.500s | 3.10× |
| 25 | 0.964s | 3.750s | 3.89× |
| 50 | 1.501s | 7.500s | 5.00× |

All 50 concurrent requests returned a non-empty answer with no crash, deadlock, or dropped
request. Wall-clock time grows sublinearly with concurrency (50 concurrent requests took
1.501s — under 8× the single-request time of 0.190s, not 50×), confirming the orchestration
layer genuinely overlaps requests (each on its own DB session, per `CopilotService`'s
per-call `AsyncSession`) rather than serializing them behind a shared lock or connection.
Real production concurrency is additionally bounded by the per-user rate limit shipped in
Phase 24 (10 requests/minute on `/api/ai/ask`) — that limiter's own correctness under
concurrent requests (exactly `limit` succeed, the rest get a clean `429`, no race) is
verified separately in
[`test_scale_hardening.py::test_ai_ask_under_concurrent_load_enforces_the_rate_limit_without_crashing`](../backend/tests/test_scale_hardening.py),
since this section's job is to measure orchestration overhead, not re-prove the rate limiter.

## Known gaps

- **No real Postgres or Redis was available to benchmark against** — every number above is
  SQLite + `fakeredis`, single-process. Re-running `backend/scripts/scale_bench.py` against
  a real `docker compose up` stack, with multiple API worker processes, is the natural next
  step before this report can inform production capacity planning.
- **No real Oracle, SMTP, or local LLM instance** exists in this environment — collector,
  notification, and AI benchmarks all measure this platform's own code around instant or
  artificially-delayed fake test doubles, never real network I/O to those systems. This
  mirrors the same standing gap already documented for every earlier phase (see
  `docs/RUNBOOK.md`).
- **Storage growth was not benchmarked at meaningful scale** (Section 4) — the seeded
  history (200–400 rows) is far too small relative to SQLite's fixed page-size overhead to
  produce a trustworthy bytes-per-database-per-day figure.
- **`GET /api/databases` (and the Fleet Overview/Databases pages built on it) silently
  truncate past 200 databases** (Section 2) — confirmed safe at the master plan's own
  10/50/100 tiers, not fixed this phase; needs API pagination plus a frontend "showing N of
  M" treatment before a fleet can safely exceed 200 databases.
- **Single-process only** — this benchmark never exercises multiple API workers or multiple
  collector processes contending for the same Postgres/Redis, which is exactly the
  production deployment shape (`docker-compose.yml` currently runs one `api` container, but
  Phase 25's own master-plan wording implies horizontal scaling is in scope for a true
  production certification). That is a distinct, larger benchmark this phase did not attempt.
- **This is a single benchmark run, not a statistically repeated one** — no variance/
  confidence-interval analysis was done across repeated runs; treat the p50/p95 figures as
  indicative, not certified SLO measurements.
