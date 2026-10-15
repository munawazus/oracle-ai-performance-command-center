# Disaster Recovery Runbook — Master Plan Phase 26

Master plan deliverable: "Prove recovery under failure" — DB unreachable, collector
restart, PostgreSQL restore, queue recovery, notification provider outage.

Scope note, consistent with this platform's own hard scope rule (master plan Sec 1.2/38.1,
"no automated remediation"): every recovery described here is either fully automatic
(the platform already handles it with no operator action) or a manual infrastructure-layer
procedure an operator runs (the platform never executes it itself). There is no
self-healing "click to recover" button in the product, by design.

## 1. Oracle database unreachable

**Fully automatic - no operator action required.** When a registered database's Oracle
instance becomes unreachable, the collector marks it `unreachable` in
`InstanceHealthSnapshot` and increments `CollectorStatus.consecutive_failures`, but keeps
retrying on every scheduled tick (`COLLECTOR_INTERVAL_SECONDS`, default 15s) — there is no
backoff or give-up state for the collector itself (unlike the notification/integration
queues, which do back off). The moment Oracle becomes reachable again, the very next tick
succeeds on its own: `consecutive_failures` resets to 0, `last_success_at` updates, and
`InstanceHealthSnapshot.reachability` flips back to `up`. Proven in
[`test_collector_recovers_automatically_once_oracle_becomes_reachable_again`](../backend/tests/test_dr_recovery.py).

One database's outage never affects another's — each database's collection cycle opens and
commits its own database session independently
([`collector_service.py`](../backend/app/services/collector_service.py)'s own design), so a
fleet-wide `run_once_all_active()` pass isolates a crash in one database's sampling from
every other database in the same round. Proven (with a Python-level crash, not just an
Oracle connection error, to cover a different failure class) in
[`test_fleet_collector_run_isolates_a_process_level_crash_in_one_database_from_the_rest`](../backend/tests/test_dr_recovery.py).

**Operator visibility**: the Health dashboard page and each database's collector-status API
(`GET /api/databases/{id}/collector-status`) surface `consecutive_failures`,
`last_error_category`, and `last_success_at` — check there first if a database looks stale.

## 2. Collector / API process restart

**Fully automatic - no operator action required.** The collector scheduler's four jobs
(Oracle collector, analytics engine, notification queue, integration queue) hold no
in-memory state of their own: every tick re-queries "all active databases" fresh from
Postgres via `MonitoredDatabaseRepository.list_active_ids()` (the Phase 25 fix), and every
pending queue item lives in a Postgres row, not in memory or in the scheduler. A process
restart (container restart, deploy, crash-and-supervisor-relaunch) therefore requires zero
recovery ceremony — the next tick after startup just works, picking up exactly where a
fresh query says it should. Proven in
[`test_collector_scheduler_start_stop_is_idempotent_and_safe_to_repeat`](../backend/tests/test_dr_recovery.py),
which also surfaced a minor timing nuance worth knowing: `AsyncIOScheduler.shutdown()`
(APScheduler 3.x) defers its actual state change via `call_soon_threadsafe` rather than
completing synchronously — harmless in production (the process exits shortly after
`lifespan` shutdown regardless), but means "is the scheduler stopped yet" isn't reliably
answerable in the same tick you called shutdown.

## 3. PostgreSQL outage and restore

**Outage detection is automatic; restore is an infrastructure-layer procedure this platform
does not perform itself.** `GET /api/ready` reports `"postgres": "unreachable"` and returns
`503` the moment the platform's own metadata database becomes unreachable, and flips back to
`"ok"`/`200` the moment it's reachable again — no restart of the API process is needed for
this to recover, since `db_is_ready()` opens a fresh connection attempt on every call. Proven
in [`test_readiness_reports_and_recovers_from_a_postgres_outage`](../backend/tests/test_dr_recovery.py).
Point this at your container orchestrator's readiness probe so traffic is held back
automatically during an outage.

Restoring Postgres itself is out of this platform's scope (master plan Sec 29's "Secrets
external secret manager compatible" and Sec 31's "Docker Compose first" both assume the
data-plane infrastructure is managed outside the app). Local dev / Docker Compose:

```bash
# Backup (run against the running postgres container)
docker compose exec postgres pg_dump -U ocmd -d ocmd -F c -f /tmp/backup.dump
docker compose cp postgres:/tmp/backup.dump ./backup.dump

# Restore into a fresh/empty database
docker compose cp ./backup.dump postgres:/tmp/backup.dump
docker compose exec postgres pg_restore -U ocmd -d ocmd --clean --if-exists /tmp/backup.dump
```

In production, use your Postgres provider's own backup/point-in-time-recovery feature
(managed Postgres services all provide one) rather than ad hoc `pg_dump`/`pg_restore` — this
platform stores no data outside Postgres that would need separate reconciliation after a
restore (Redis holds only rate-limit counters, which are safe to lose - see below).

**Known gap**: no backup automation, retention policy, or scheduled backup job exists inside
this platform itself (master plan's Administration → Retention screen is itself unbuilt —
see `docs/RUNBOOK.md`'s Known Gaps). Backups are entirely an infrastructure-layer
responsibility today.

## 4. Redis outage and recovery

**Low-severity by design - Redis holds no durable state.** Every queue this platform has
(notification delivery, integration delivery) is backed entirely by Postgres rows
(`notification_events`, `integration_deliveries`); Redis is used only for the Phase 24
rate limiters. `RateLimitMiddleware` already fails open on any Redis exception (a rate-limit
outage degrades to "no rate limiting," never a full API outage — see `docs/THREAT_MODEL.md`),
and `GET /api/ready` reports `"redis": "unreachable"` / `503` while it's down, recovering the
moment Redis comes back — proven in
[`test_readiness_reports_and_recovers_from_a_redis_outage`](../backend/tests/test_dr_recovery.py).
Losing Redis's data entirely (a fresh, empty Redis instance) loses nothing worth recovering —
in-flight rate-limit windows just reset, which is the same effect as the window naturally
expiring.

## 5. Queue recovery (notification / integration delivery)

**A real bug was found and fixed this phase.** `NotificationService.process_pending()` and
`IntegrationService.process_pending()` used to commit their whole batch (up to 50 items) in
one transaction, committed once by the caller after the loop finished
(`app/collectors/scheduler.py`). A process killed midway through a batch — after some emails
or webhooks had already gone out over real SMTP/HTTP, but before the commit that would have
recorded them as `SENT` — would leave those already-delivered items still `PENDING`/
`RETRYING`, and the very next tick would resend them. For a HIGH/CRITICAL incident, that
means the on-call recipient could get duplicate alert emails or duplicate webhook posts.

Fixed by committing after every individual item instead of once per batch, in both services.
A mid-batch crash can now only cost the one in-flight item's own progress — which is always
safe to retry, since it never reached `SENT` — never re-does already-completed sends. Proven
directly (simulating a process kill via an uncaught `SystemExit`, which — like a real OS-level
kill — runs no Python exception handler at all) in
[`test_notification_queue_survives_a_mid_batch_crash_without_resending_sent_events`](../backend/tests/test_dr_recovery.py)
and the equivalent integration-queue test in the same file.

**Operator visibility**: each incident's drawer shows notification and integration delivery
status/history; `GET /api/notifications`/integration delivery list endpoints expose the same
data for scripted checks.

## 6. Notification provider (SMTP) outage and recovery

**Fully automatic - no operator action required, up to `MAX_ATTEMPTS` (5).** The retry/backoff
schedule (`[60, 300, 900, 3600]` seconds) already proven in Phase 16/21's own test suite
(`test_transient_failure_retries_then_eventually_sends`,
`test_incident_is_never_lost_when_notification_fails` in
[`test_notification_service.py`](../backend/tests/test_notification_service.py)) means a
transient SMTP outage recovers on its own the next time `process_pending()` runs after the
backoff window elapses — the incident itself is never affected by SMTP being down. The same
is true for a webhook/Slack/Teams endpoint outage on the integration side.

**Manual action needed only after exhaustion**: once an event reaches `FAILED` (5 failed
attempts), nothing resends it automatically — there is currently no "retry now" admin action
in the UI or API for an exhausted event. Confirm the underlying SMTP/webhook configuration is
fixed (Administration → Notifications / Integrations → Test), then either wait for the next
HIGH/CRITICAL event on that incident to queue a fresh notification, or re-open the
investigation manually. This is a known gap, not a defect: exhaustion is a deliberate signal
that automatic retry has stopped helping and a human should look.

## Known gaps

- **No backup/restore automation inside the platform itself** (Section 3) — this is an
  infrastructure-layer responsibility today; no scheduled backup job, retention policy, or
  "restore point" UI exists.
- **No "retry now" action for an exhausted (`FAILED`) notification or integration delivery**
  (Section 6) — an operator must wait for the next qualifying incident event or intervene
  manually at the database layer.
- **This phase's tests could not induce a real Postgres, Redis, Oracle, or SMTP outage** —
  the same standing gap as every earlier phase (see `docs/RUNBOOK.md`'s Known Gaps). Every
  test here proves this platform's own code behaves correctly around a simulated failure
  (a monkeypatched readiness check, a broken fake Redis client, a sender that raises), not
  that the real infrastructure component itself fails and recovers the way this runbook
  assumes.
- **No chaos-engineering pass** (randomly killing real processes under real load) was
  performed — master plan Phase 27 (Adversarial QA) is the next phase that could add this;
  it was explicitly out of scope here.
- **Collector scheduler shutdown timing** (Section 2) — `AsyncIOScheduler.shutdown()` is
  deferred by one event-loop tick, discovered while writing this phase's own tests. Harmless
  in production, but means "the scheduler has fully stopped" isn't synchronously observable
  immediately after calling shutdown.
