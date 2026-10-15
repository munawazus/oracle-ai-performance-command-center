# Production Readiness Report — Master Plan Phase 28

> **Superseded on the infrastructure claims below.** This document is a snapshot of Phase 28
> (`v0.1.0-rc1`, commit `0f0b35c`) and is left historically intact. Every "NEVER EXECUTED" /
> "never used" / "never sent through a real relay" claim about Postgres, Redis, Oracle, SMTP,
> and a local LLM in Sec 1-9 below was resolved by Enterprise Readiness Plan Phase A
> (`docs/ENTERPRISE_READINESS_PLAN.md`, items A.1-A.6) - see
> `docs/PRODUCTION_CERTIFICATION.md` for the current, re-verified gate assessment. The
> control-inventory gap in Sec 9 and the fleet-scale load gap in Sec 3/5 are the two claims
> here that remain accurate as written.

Master plan Sec 52 requires this declaration to include exact test suites run, pass/fail
counts, known limitations, open risks, supported Oracle versions/editions, infrastructure
requirements, and specific evidence for email alerting, AI tool safety, and UI control
coverage — and explicitly warns: **"Do not declare production ready from test counts
alone."** This document follows that instruction literally. The verdict at the bottom is not
"production ready" — it names exactly what stands between this release candidate and that
claim, most of which is one fact repeated in different forms: **nothing in this codebase has
ever been exercised against real Oracle, SMTP, LLM, Postgres, or Redis infrastructure**, only
against faithful test doubles of each. Production certification is master plan Phase 29, a
separate, later phase from this one.

## 1. Exact test suites run, with pass/fail counts

| Suite | Location | Environment | Result |
|---|---|---|---|
| Backend unit + component | `backend/tests/` (277 tests) | SQLite + `fakeredis`, in-process, single worker | **277 / 277 passed** |
| Backend lint | `ruff check app tests` | — | Clean |
| Backend type check | `mypy app` | — | Clean (4 pre-existing `reportlab` stub-import warnings only) |
| Backend security lint | `bandit -r app` | — | Clean, 0 findings |
| Backend dependency scan | `pip-audit -r requirements.txt` | — | Clean, 0 known vulnerabilities |
| Frontend unit + component | `frontend/` Vitest (20 files, 70 tests) | jsdom | **70 / 70 passed** |
| Frontend type check | `tsc -b` | — | Clean |
| Frontend lint | `eslint .` | — | 0 errors, 3 pre-existing warnings (unrelated files) |
| Frontend production build | `vite build` | — | Succeeds, 6 chunks emitted |
| Frontend dependency scan | `npm audit --audit-level=high` | — | Wired into CI; not re-run for this report |
| **Real-infrastructure integration** | `tests/integration/` (4 tests) | **Real Postgres 16 + real Redis 7** | **NEVER EXECUTED — see Sec 4** |
| **Browser E2E** | `frontend/e2e/login-flow.spec.ts` (Playwright) | Real browser | **NEVER EXECUTED — see Sec 4** |
| Migration reversibility | `alembic upgrade head` → `downgrade base` → `upgrade head` | Scratch SQLite | **Passed**, all 9 migrations both directions — proven this cycle, not merely assumed from `downgrade()` existing (see `docs/UPGRADE_AND_ROLLBACK.md`) |

**277 backend + 70 frontend = 347 automated tests currently pass.** That number is
necessary, not sufficient — the two suites specifically designed to catch what those 347
cannot (real database/cache behavior, real browser behavior) have never run even once.

## 2. Known limitations

Consolidated from every phase's own documentation — not repeated in full here, followed to
source:

- **No real Oracle instance ever used** — collector, capability detection, and telemetry
  sampling are proven against a fake connector matching `python-oracledb`'s interface only.
  (`docs/RUNBOOK.md` Known Gaps)
- **No real SMTP relay ever used** — email delivery proven against a fake `EmailSender` only.
  (`docs/RUNBOOK.md` Known Gaps)
- **No real local LLM server ever used** — AI gateway/Copilot/RCA proven against a fake
  `LLMClient` only; whether a real gpt-oss-120b deployment follows the JSON-shape
  instructions reliably is unverified. (`docs/RUNBOOK.md`, `docs/THREAT_MODEL.md` Sec 6)
- **No real webhook/Slack/Teams endpoint ever used** — integration delivery proven against a
  fake `WebhookSender` only. (`docs/RUNBOOK.md` Known Gaps)
- **No real Postgres or Redis server ever reachable in this environment** — every phase from
  Security Hardening onward (rate limiting, scale benchmarks, DR/recovery proofs) runs
  against SQLite + `fakeredis`. (`docs/SCALE_TEST_REPORT.md`, `docs/DR_RUNBOOK.md`)
- **No multi-tenant isolation** — every authorization check is resource-class scoped, not
  per-tenant; safe only under a one-organization-per-deployment model, a documented scope
  decision. (`docs/THREAT_MODEL.md` Sec 1/4.2)
- **Activity feed has no collector** — it honestly reports unavailable data. SQL
  Intelligence (master plan Phase 9, Enterprise Readiness Plan D.1), Capacity (master plan
  Phase 5, Enterprise Readiness Plan D.2), Statspack integration (master plan Phase 11,
  Enterprise Readiness Plan D.3), and Change Intelligence (master plan Phase 14, Enterprise
  Readiness Plan D.4) are no longer in this list — all four collectors and their analytics
  are real, with real UI (a dedicated page for SQL Intelligence/Capacity/Change Intelligence,
  an additive panel for Statspack). (`README.md`)
- **RCA quality benchmark suite now exists and has run for real** (Master plan Phase 20,
  Enterprise Readiness Plan E.1) — no longer a gap, but the real result belongs here anyway:
  a 5-scenario gold-incident set, scored by 5 deterministic checks, run against a real local
  model (Ollama + `qwen2.5:7b-instruct`) found zero hallucinated identifiers and zero
  unsupported-confidence claims, but the model declined to name a root cause on 4 of 5
  scenarios even where the evidence clearly supported one. See
  [docs/RCA_BENCHMARK_REPORT.md](RCA_BENCHMARK_REPORT.md).
- **No systematic "every UI control has a test" audit has been run as its own pass** — per-
  page functional tests exist; the master plan's Sec 34 "Every Button Works" program (unique
  control IDs, negative tests, accessibility checks per control) was never run as a
  standalone exercise.
- **DNS-rebinding SSRF protection is narrowed, not eliminated** — resolve-then-connect check
  at both config-time and send-time; full elimination needs socket-level IP pinning.
  (`docs/THREAT_MODEL.md` Sec 5.4)

## 3. Open risks

Ranked by what would actually hurt if wrong in production:

1. **The real-Postgres/Redis integration suite has never run.** Everything about connection
   pooling, transaction isolation, and Redis-backed rate limiting under this platform's
   actual production database engine is inferred from SQLite/fakeredis behavior, which is
   not guaranteed equivalent (see Sec 4).
2. **No real Oracle instance has validated the collector's actual SQL against a live V$
   view schema.** A column-order assumption or a version-specific view shape this platform
   hasn't seen would only surface against real Oracle. (`docs/RUNBOOK.md`)
3. **Multi-tenant isolation does not exist.** Deploying this platform to serve more than one
   organization from a single instance would be a horizontal-IDOR risk on day one.
4. **No adversarial testing against real infrastructure** — Phase 27's SQL-injection,
   prompt-injection, and SSRF audits were all static/code-level; none were fired at a real
   Oracle instance, a real LLM, or a real DNS resolver under attacker control.
5. **Scale numbers are not production-representative** — Phase 25's benchmark ran
   single-process against SQLite; real multi-worker-process behavior under real Postgres
   contention is unmeasured.

## 4. What it would take to close the two biggest gaps

Both are concrete, bounded, and already have the code written for them:

- **Real-infrastructure integration tests** (`tests/integration/`, wired into
  `.github/workflows/ci.yml`): this repository has no connected git remote and no CI runner,
  so the workflow has never executed even once, despite already spinning up real Postgres 16
  and Redis 7 service containers and running exactly the tests this report is missing.
  Connecting this repo to GitHub (or running `docker compose -f <a postgres+redis compose
  file> up` locally and pointing `DATABASE_URL`/`REDIS_URL` at it) and running
  `pytest tests/integration` would close this gap directly, no new code required.
- **Playwright E2E** (`frontend/e2e/login-flow.spec.ts`): the spec is authored and the
  config exists; `npx playwright install` (browser binaries) was not run in this environment.
  Running it against a real deployed instance would close this gap directly, also no new
  code required.

Neither of these needs new engineering — both need infrastructure this development
environment doesn't have, run once.

## 5. Supported Oracle versions/editions

Per the master plan's own SE2-first scope: Standard Edition 2 is the primary target, with
Enterprise Edition also supported through the same code path (`edition_hint` accepts
`SE2`/`EE`/`UNKNOWN`, detected from the `V$VERSION` banner text — `detect_edition()` in
`backend/app/oracle/capability_probe.py`). The collector's V$ queries target Oracle's stable,
long-standing base views (`V$SESSION`, `V$SYSTEM_EVENT`, `V$INSTANCE`, etc. — see
`backend/app/oracle/allowlist.py`), which have not materially changed shape across recent
Oracle releases, but **no specific version has been validated against a live instance** —
this is inferred from documentation and driver compatibility (`oracledb==4.0.2`, thin mode,
no Oracle Client install required), not observed. Do not publish a specific "tested against
19c/21c/23ai" claim until Sec 4's point 2 gap is closed.

## 6. Infrastructure requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.12 | Backend runtime (CI-pinned) |
| Node.js | 22 | Frontend build (CI-pinned) |
| PostgreSQL | 16 (alpine image in `docker-compose.yml`) | Primary metadata store; SQLite supported for local dev/tests only |
| Redis | 7 (alpine image) | Rate-limit counters only — not a source of truth for any queue (see `docs/DR_RUNBOOK.md` Sec 4) |
| `python-oracledb` | 4.0.2, thin mode | No Oracle Instant Client install required |
| Deployment | Docker Compose (single-node, builds from source) | Master plan Sec 31 "Phase A" — no Kubernetes/registry pipeline yet |
| Local LLM (optional) | Any OpenAI-compatible server (vLLM/Ollama/llama.cpp), `gpt-oss-120b` primary / `gpt-oss-20b` fallback | `AI_ENABLED=false` by default; platform runs fully without it |

## 7. Evidence for email alerting

HIGH/CRITICAL-only trigger, per-incident-per-severity dedup, `[60, 300, 900, 3600]`s
retry/backoff up to 5 attempts, delivery survives SMTP being fully down (incident is never
lost), and — as of this cycle's DR/recovery work — a per-item commit that makes a mid-batch
process crash unable to resend an already-delivered alert. Test coverage:
`backend/tests/test_notification_service.py` (dedup, escalation, retry, exhaustion,
incident-survives-failure) and `backend/tests/test_dr_recovery.py` (mid-batch-crash
recovery). **Never sent through a real SMTP relay** — see Sec 2.

## 8. Evidence for AI tool safety

The tool layer (`backend/app/ai/tools.py`) has no code path to an Oracle connection, an
Oracle credential, or arbitrary SQL execution — every tool reads only this platform's own
Postgres telemetry. The system prompt (`backend/app/ai/prompts.py`) explicitly labels
Oracle-sourced evidence (session module/program/machine/username, SQL text) as untrusted
data, never instructions — confirmed this cycle to survive an actual adversarial payload
planted in session data, not just asserted to exist
(`backend/tests/test_adversarial_qa.py::test_ai_copilot_prompt_frames_adversarial_session_data_as_untrusted_evidence`).
**Never tested against a real model** — a fake `LLMClient` cannot be prompt-injected in any
meaningful sense; whether a real gpt-oss-120b deployment actually resists a crafted payload
is unverified (see Sec 2).

## 9. Evidence for UI control coverage

Every shipped page has its own component/integration test suite (70 Vitest tests across 20
files) proving core interactions work against the real API contract. **No standalone
"every control inventoried, every control tested, negative-path-and-accessibility-checked"
pass has been run** as its own deliverable — master plan Sec 34's "Every Button Works" QA
program was never executed as a dedicated exercise, and the one Playwright E2E spec that
exists has never run (Sec 1/4). This is the least-verified of the three areas the master
plan specifically calls out.

## 10. Verdict

**Not production certified — and not claimed to be.** This release candidate has real,
extensively tested code behind 21 of 30 master-plan phases (347 automated tests passing,
zero open critical findings across two security-focused phases), and two of this cycle's
own phases each found and fixed a genuine bug before it could reach anyone. But every
gap listed in Sec 2 through Sec 4 is real, specific, and — per master plan Sec 52's own
instruction — this report declines to round that up to "production ready" on test-count
momentum alone. The two most valuable next actions are both bounded and already coded:
run the real-Postgres/Redis integration suite once, and run the Playwright E2E spec once.
Master Plan Phase 29 (Production Certification) should not proceed until those two gaps, at
minimum, are closed with real evidence rather than inferred from test doubles.
