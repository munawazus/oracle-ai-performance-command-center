# Upgrade and Rollback — Master Plan Phase 28

This is the first release candidate (`v0.1.0-rc1`), so there is no prior version to upgrade
*from* yet. This document establishes the procedure future releases will follow, and records
the one fact that actually matters for trusting it: the full migration chain has been proven
reversible end-to-end (see Verification below), not just assumed reversible because every
migration file happens to define a `downgrade()` function.

## Migration inventory

Nine migrations, one linear chain, single head (`alembic heads` confirms no branching):

| Revision | Depends on | Adds |
|---|---|---|
| `ebfa48c7d02b` | (base) | Initial platform schema — users, roles, permissions, sessions, databases, credentials, audit |
| `de862c5c94d0` | `ebfa48c7d02b` | Capability probe fields |
| `02ea032d7836` | `de862c5c94d0` | Collector telemetry tables (sessions, waits, instance health) |
| `f92f004c2431` | `02ea032d7836` | Analytics: baselines and anomalies |
| `8e17154bd334` | `f92f004c2431` | Incidents and incident events |
| `51b797c56a5f` | `8e17154bd334` | Notification settings and events |
| `7daf8ca203cc` | `51b797c56a5f` | Reports |
| `61c1c7a676de` | `7daf8ca203cc` | Fleet management: groups and tags |
| `8e83c7fc4c3d` | `61c1c7a676de` | Integrations: webhooks, Slack, Teams |

## Upgrade procedure

1. Read this release's [RELEASE_NOTES.md](RELEASE_NOTES.md) for anything version-specific
   (config changes, new required environment variables) — none apply yet for `v0.1.0-rc1`
   itself, but a future release's notes are the authoritative source, not this checklist.
2. **Back up Postgres first** — see [DR_RUNBOOK.md](DR_RUNBOOK.md) Sec 3 for the
   `pg_dump`/managed-provider procedure. Never upgrade a production deployment without a
   fresh backup in hand.
3. Pull/build the new application code.
4. Run migrations: `cd backend && alembic upgrade head`. This is safe to run against a
   database already at an earlier revision in the same chain — Alembic only applies the
   migrations between the current revision and `head`.
5. Restart the API/collector process (`docker compose up -d --build` in the Docker Compose
   deployment, or your platform's equivalent rolling restart).
6. Verify `GET /api/ready` returns `200` with both `postgres` and `redis` reported `ok`
   before considering the upgrade complete.
7. Spot-check one collector cycle actually ran (`GET /api/databases/{id}/collector-status`
   on a known-active database) and one page of the UI loads real data.

Redis needs no migration step — it holds only rate-limit counters (see
[DR_RUNBOOK.md](DR_RUNBOOK.md) Sec 4), which are safe to lose entirely and require no
upgrade handling at all.

## Rollback procedure

If an upgrade misbehaves:

1. **Application code**: redeploy the previous release's image/build. Since this platform
   builds from source (`docker-compose.yml`, no registry pipeline yet — see
   [RELEASE_NOTES.md](RELEASE_NOTES.md)), this means checking out the previous release's git
   tag and rebuilding.
2. **Database schema**: only run `alembic downgrade <previous-revision>` if the new
   migration is confirmed to be the actual problem, and only after re-confirming the backup
   from step 2 of the upgrade procedure exists. Downgrading is a real schema change with
   real data-loss potential for anything added by the migration being reverted (e.g.
   downgrading past the Reports migration drops the `reports` table and everything in it) —
   prefer rolling back application code alone first, since most upgrade failures are
   applicaton-level, not schema-level, and the schema is additive-only across this chain (no
   migration in this release renames or repurposes an existing column).
3. If schema rollback is genuinely needed and the backup from step 2 is available, restoring
   from that backup is safer and more predictable than trusting a `downgrade()` path against
   a database that has since accumulated new production data the migration was never
   designed to unwind.
4. Verify `GET /api/ready` again after any rollback step, the same as after an upgrade.

## Verification (what's actually been proven, not assumed)

Every migration's `downgrade()` was exercised for real while preparing this release, not
just confirmed to exist: `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade
head` against a scratch SQLite database completed cleanly through the full 9-migration chain
in both directions, with no error at any step. This proves the *SQLite* path end-to-end (the
same engine this repository's own test suite runs against); the *Postgres* path (production)
has not been exercised the same way in this environment, since no real Postgres server is
reachable here (see [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)) — SQLite and Postgres
DDL are not always equivalent (e.g. `ALTER COLUMN` semantics differ), so this is a real,
named gap, not a formality.
