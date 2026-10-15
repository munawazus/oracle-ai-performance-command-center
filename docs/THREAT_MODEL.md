# Threat Model

Master Plan Phase 24 (security hardening) deliverable: "Threat model updates." This is a
living document - update it whenever a new trust boundary, external dependency, or data flow
is added, not just during a dedicated security phase.

## 1. Scope and non-goals

In scope: the platform itself (backend API, frontend, database, the collector/analytics/
notification/integration workers, the AI gateway integration). Out of scope: the security of
a customer's own Oracle instance, SMTP relay, webhook receiver, or LLM server - this platform
is a client of those systems, not responsible for hardening them.

Automated remediation is architecturally absent (master plan Sec 1.2/38.1) - there is no
code path anywhere in this platform that can modify, kill, or reconfigure a monitored Oracle
database. This removes an entire class of "AI/attacker causes real damage to production data"
risk by construction, not by policy.

Also out of scope, as a Phase 27 finding made explicit (see Sec 4.2): multi-tenant isolation.
This platform assumes one deployment monitors one organization's own Oracle fleet - every
authorization check is resource-class scoped (a permission like `database:view`), never
per-record/per-tenant scoped, because there is currently no tenant boundary to scope against.

## 2. Assets, ranked by impact if compromised

1. **Oracle monitoring-account credentials** (`database_credentials.secret_ref` -> `secrets`
   table, Fernet-encrypted at rest via `SECRET_ENCRYPTION_KEY`). Compromise = read access to
   every registered Oracle instance's permitted telemetry views.
2. **`SECRET_ENCRYPTION_KEY` / `JWT_SECRET_KEY`** (deployment secrets, never committed -
   `.env`, gitignored). Compromise = decrypt every stored secret / forge any JWT.
3. **User credentials and sessions** (Argon2 password hashes, JWT access/refresh tokens,
   `sessions` table for refresh-token revocation).
4. **SMTP credentials, integration signing secrets** - same `secrets` table, same blast
   radius class as #1 but lower-value targets (outbound notification channels, not a
   database).
5. **Audit log integrity** (`audit_events`) - the record of who did what; not encrypted (not
   sensitive by design - see Sec 5.7) but must not be forgeable by a non-admin actor.
6. **Platform availability** - collector/analytics/notification/integration scheduler jobs;
   a stalled or crashed scheduler silently stops all monitoring, which is itself a security-
   relevant failure (undetected incidents).

## 3. Trust boundaries

```
[Browser]                     untrusted input, holds a JWT access token + refresh cookie
    |  HTTPS (deployment-dependent - see Sec 5.8)
    v
[FastAPI backend]              the only component with any secret material
    |             |            |                  |                |
    v             v            v                  v                v
[PostgreSQL]  [Redis]   [Monitored Oracle]   [SMTP relay]   [Webhook/Slack/Teams targets]
              (rate                                                |
               limit,                                        [Local LLM gateway]
               sessions)
```

- **Browser <-> API**: the only boundary that accepts input from an anonymous or low-
  privilege party. Everything else is either the platform's own infrastructure or an
  admin-configured outbound target.
- **API <-> Postgres**: fully trusted, same deployment unit. All access is via SQLAlchemy's
  parameterized query builder - no raw SQL string interpolation exists anywhere in this
  codebase (verified by code review; see Sec 5.1).
- **API <-> Redis**: fully trusted, used for rate-limit counters and refresh-session
  bookkeeping only - never a store of secret material or telemetry.
- **API <-> a monitored Oracle instance**: the platform is the *client*; a compromised or
  malicious Oracle target is itself a threat actor against the platform (see Sec 4.3).
- **API <-> SMTP / webhook-Slack-Teams targets / LLM gateway**: all admin-configured
  (`notification:configure`), all outbound-only, all behind the same test-double-verified,
  never-blocks-core-function delivery pattern (Sec 5.5).

## 4. Attacker classes

### 4.1 Unauthenticated internet-facing attacker

Can reach `/api/auth/login`, `/api/health`, `/api/ready`, and nothing else that returns real
data (every other route is behind `require_permission`/`get_current_user`, tested explicitly
per-endpoint - a 401-without-a-token regression test exists for every route file). Mitigated
by: per-IP login rate limiting (30/5min, `RateLimitedError` 429 with `Retry-After`), the
blanket per-IP `RateLimitMiddleware` floor on every other `/api/*` path, account lockout
(5 failed attempts / 15 minutes), Argon2 password hashing, and the security-headers
middleware narrowing what a reflected-content or clickjacking attempt could do (there is
close to no reflected user content in this API's responses to begin with).

### 4.2 Authenticated, low-privilege user (VIEWER/AUDITOR/OPERATOR)

RBAC is enforced at the API/dependency layer (`require_permission`), never only in the
frontend - a VIEWER hitting `POST /api/incidents/{id}/acknowledge` directly gets 403
regardless of what the UI shows them (tested per-role, per-endpoint across every phase's own
test suite). Every denial is audited (`authz.denied`). The remaining residual risk is a
*missing* `require_permission` on a route added without a test - mitigated by the
established pattern (every new route in every phase to date pairs a permission dependency
with an explicit 401/403 regression test) but not mechanically enforced (no route-inventory
lint rule exists yet - see Sec 6). A Phase 27 audit enumerated all 47 routes across every
route file and found none missing an authorization dependency, and no permission-level
mismatch (a mutating action gated behind a weaker permission than its own sensitivity).

**Phase 27 finding, documented not fixed**: authorization everywhere is *resource-class*
scoped (`database:view`, `incident:manage`, ...), never *per-record* scoped. Any `{id}`-
scoped route (a database, incident, report, integration, group) fetches by ID after checking
only the class-level permission - there is no ownership or tenant check on the specific
record. This is safe **only** under this platform's actual deployment model: one deployment
monitors one organization's own Oracle fleet, so every authenticated user who holds
`database:view` is *supposed* to be able to see every database in that fleet - there is no
narrower boundary to enforce. The master plan's own data model (Sec 8) names an
`organizations/tenants` entity ("Isolation and ownership") that **was never implemented** -
no `organization_id`/`tenant_id` column exists anywhere in this schema. If this platform were
ever deployed to serve multiple organizations' fleets from one shared instance, every
`{id}`-scoped route would become a horizontal-IDOR path for any authenticated Viewer in any
organization to read any other organization's databases, incidents, and reports by
ID-guessing or enumeration. This was an unexamined assumption before Phase 27, which made it
an explicit, documented scope decision; **Enterprise Readiness Plan C.3 (2026-09-04)** turned
that into a ratified deployment decision, not just an inferred one: **this platform ships as
one deployment per customer, single-organization-per-deployment, permanently** - not "not yet
multi-tenant," a decided model. `organization_id` + query-scoping work is therefore explicitly
**not** planned for Phase D or any later phase; if that decision is ever revisited, it needs
its own dated plan item, not a silent assumption the way this one started.

### 4.3 A compromised or malicious monitored Oracle instance

This is the platform's most unusual attacker class: session `program`/`module`/`username`,
SQL text, and other collected telemetry *originate from a system this platform does not
control*. Two places this matters:

- **AI evidence grounding** (master plan Sec 23): the AI tool layer's system prompts
  explicitly instruct the model to treat all collected evidence as *data to reason about*,
  never as instructions, and the tool layer itself has no code path to an Oracle connection,
  an Oracle credential, or arbitrary SQL execution - a poisoned `V$SESSION.program` value
  can influence what the model *says*, never what it *does*. As of Enterprise Readiness Plan
  D.1, `get_top_sql`/`get_sql_plan` make this concrete: `V$SQL.SQL_TEXT` (up to 1000
  characters, attacker-influenceable by anyone with SQL-injection-adjacent access to a
  monitored application, or by a compromised instance itself) and `V$SQL_PLAN` operation/
  object names are now real evidence fields subject to the same framing, gathered
  automatically into every Copilot/RCA query scoped to a database - by far the largest
  attacker-controllable string this platform puts in a prompt. Locked in by
  `test_ai_copilot_prompt_frames_adversarial_sql_text_as_untrusted_evidence`
  (`backend/tests/test_adversarial_qa.py`). As of Enterprise Readiness Plan D.2,
  `get_tablespace_trend` adds `DBA_TABLESPACES.TABLESPACE_NAME` to the same framing too - a
  much lower real risk than SQL text (short, DBA-assigned, restricted-charset identifier), so
  proportionately no dedicated adversarial test was added for it, unlike SQL text's. As of
  Enterprise Readiness Plan D.3, `get_statspack_summary` carries the same SQL-text risk as
  `get_top_sql` (`STATS$SQL_SUMMARY`'s `sql_id`-joined text, cross-referenced against this
  platform's own D.1 `SqlStatement` table) plus `STATS$SYSTEM_EVENT.EVENT` wait-class names -
  all subject to the same framing, but this tool is deliberately on-demand-only (never
  auto-gathered), so the exposure window is narrower than `get_top_sql`'s automatic inclusion.
  As of Enterprise Readiness Plan D.4, `get_change_history` adds `V$SYSTEM_PARAMETER`
  parameter names/values to the same framing - Oracle itself never exposes credentials or
  secrets through this view, but a parameter *value* (e.g. `control_files`, `service_names`)
  is still attacker-influenceable evidence text from the DBA's own instance, subject to the
  same "data to reason about, never instructions" rule; also on-demand-only, same narrow
  exposure window as `get_statspack_summary`.
- **Report/PDF export**: incident titles and resolution notes (themselves derived from
  anomaly detection over Oracle-observed metrics, not raw Oracle text, but session
  `username`/`machine`/`program` values do flow into report content) are XML-escaped before
  reaching reportlab's Paragraph markup parser (Phase 21), so a session field crafted to
  look like markup can't inject formatting or, more importantly, break the PDF structure.

### 4.4 A malicious or careless platform admin

An ADMIN/DBA can configure an integration target, SMTP relay, or database host pointing
anywhere they choose - this is *intentional* (they are a trusted operator, same trust level
as someone with production database credentials generally). The SSRF guard (Sec 5.4) is not
a defense against a *malicious* admin - a `notification:configure` holder can still request
`ALLOW_INTERNAL_INTEGRATION_TARGETS=true` be set by whoever controls deployment env vars, and
Oracle/SMTP host fields have no equivalent SSRF check at all yet (see Sec 6). It exists
primarily to catch *accidental* misconfiguration (pasting an internal URL by mistake) and to
raise the bar against a compromised admin *session* (stolen JWT) being immediately usable to
pivot into internal infrastructure via a freshly-created webhook integration.

## 5. Control inventory (what exists today, and why it's believed sufficient)

### 5.1 SQL injection

Every database query against this platform's own Postgres repository goes through
SQLAlchemy's async ORM query builder (`select(...)`, `.where(Column == value)`) - there is no
raw SQL string interpolation anywhere in `app/repositories/` or `app/services/`. Reports/AI
tool queries that accept user-supplied filter values (report `report_type`, incident
`status`, etc.) pass them as bound parameters via the same ORM layer, never string-formatted
into a query.

The `app/oracle/*_probe.py` modules (`capability_probe.py`, `sql_probe.py`,
`tablespace_probe.py`, `statspack_probe.py`) are a deliberate, narrower exception: each builds
an f-string query against the *monitored Oracle instance* (not this platform's own Postgres),
guarded by the same two-part pattern everywhere it appears (the 12 `# nosec B608` occurrences
in this codebase, all reviewed, all false positives per bandit's own flag) - a bound int/list
of ints validated in Python *before* interpolation (`top_n`, `max_snapshots`, `snap_ids`,
etc.), and/or a view/table name checked against a fixed allowlist
(`app/oracle/allowlist.py`'s `ALLOWLISTED_VIEWS`/`STATSPACK_ALLOWLISTED_TABLES`) - never a
request-supplied or DBA-supplied identifier. Statspack's own tables
(`PERFSTAT.STATS$SNAPSHOT`/`STATS$SYSTEM_EVENT`/`STATS$SQL_SUMMARY`, Enterprise Readiness Plan
D.3) needed a second, separate allowlist tuple rather than folding into `ALLOWLISTED_VIEWS`,
since they're ordinary tables owned by the `PERFSTAT` schema, not `V$`/`DBA_*` views - every
query site prefixes the fixed literal `"PERFSTAT."`, never a schema name from anywhere else.
Statspack itself is a free, bundled Oracle feature requiring no Diagnostics/Tuning Pack
license - unlike the `DBA_HIST_*`/`V$ACTIVE_SESSION_HISTORY` (AWR/ASH) family already in
`BLOCKLISTED_VIEWS`, which is separately licensed and this platform never queries.

### 5.2 Authentication and session management

Argon2id password hashing (`argon2-cffi`), short-lived JWT access tokens (15 min default) +
longer-lived refresh tokens in an `httponly`, `samesite=lax` cookie (`secure=true` in
production), refresh-token revocation via the `sessions` table (logout invalidates
server-side, not just client-side), and per-account lockout after repeated failures.

### 5.3 Authorization

See Sec 4.2 - `require_permission` at the API/dependency layer, tested per-route.

### 5.4 SSRF (Phase 24, hardened Enterprise Readiness Plan C.1)

`app/security/ssrf_guard.py` blocks an `Integration.target_url` that resolves to a private,
loopback, link-local, multicast, reserved, or unspecified address (or the hostname
`localhost`/`metadata.google.internal`), covering the accidental-misconfiguration and
stolen-session-pivot cases in Sec 4.4. Checked at *configuration time* (create/update) AND
again, as of Phase 27, immediately before every send (`app/integrations/webhook_sender.py`) -
adversarially audited against six specific bypass techniques (DNS rebinding, HTTP redirect,
IPv6 loopback/private encodings, hex/decimal/octal IP literals, multi-address `getaddrinfo`
responses, and the `allow_internal` escape hatch); five of six were confirmed blocked by
existing code (IPv6 handling is uniform across `IPv4Address`/`IPv6Address`, all resolved
addresses are checked not just the first, redirects are not followed, `allow_internal`
defaults false everywhere).

**DNS-rebinding gap closed (C.1).** The residual gap this section used to name - a
resolve-then-connect check can never fully close the race against the actual socket connect,
only pinning the connected socket's own IP does - is now closed for webhook delivery.
`app/security/ssrf_transport.py`'s `SSRFSafeAsyncTransport` replaces httpx's default
transport for every outbound webhook send: its custom `httpcore` network backend resolves
the target hostname exactly once, validates every candidate IP, and connects the raw TCP
socket directly to a validated IP literal - never to the hostname a second time. There is no
second resolution left for an attacker's DNS server to answer differently. TLS SNI/
certificate verification is unaffected (httpcore's own connection layer calls
`start_tls(server_hostname=...)` with the original hostname regardless of which IP the
socket connected to), so this does not weaken certificate validation. Verified against a real
external HTTPS endpoint and a real blocked loopback target (`backend/tests/
test_ssrf_transport.py`), not just mocks. **Still not applied to Oracle/SMTP host fields** -
Oracle connections don't go through httpx at all (this technique is httpx/httpcore-specific)
and remain a separate, undone item; SMTP (`aiosmtplib`, also not httpx) is the same.

### 5.5 External-dependency failure isolation

SMTP, webhook/Slack/Teams delivery, and the AI gateway can all be entirely unreachable
without affecting incident persistence, telemetry collection, or any other core function -
each has its own outbox table (`notification_events`, `integration_deliveries`) with
retry/backoff and an honest `FAILED` terminal state, never a silent drop and never a blocked
write path. `RateLimitMiddleware` itself fails *open* (allows the request, logs a warning) if
Redis is unreachable, for the same reason - a rate limiter must never become a full outage.

### 5.6 Secrets

Never logged (grep-verified: no `logger.*` call anywhere passes a secret/password/token
value), never returned by any API response (`has_secret`/`has_smtp_password` booleans only),
guarded against accidental inclusion in audit metadata by `AuditService`'s
`_FORBIDDEN_METADATA_KEYS` check (raises rather than silently logging if a caller ever passes
`password`/`secret`/`token`/`ciphertext`/`access_token`/`refresh_token` as a metadata key).
Encrypted at rest (Fernet/AES) via `SECRET_ENCRYPTION_KEY`, which must itself come from a
real secret manager in production (this platform's own encryption provider is explicitly
documented as a development/single-node default - see `LocalEncryptedSecretProvider`'s own
docstring).

**Phase 27 finding, fixed**: `RequestValidationError`'s handler (`app/main.py`) stripped
Pydantic's `ctx` key from error bodies but not `input` - Pydantic v2 echoes the raw
*submitted* value there for any field that fails validation, so a password/secret/token
field rejected for being too long, the wrong type, or otherwise invalid would reflect that
credential verbatim in the 422 response body (and from there into any error tracker or proxy
log that captures response bodies). Fixed by stripping `input` from every validation error
uniformly, not by naming specific "known secret fields" - a maintained field-name list is
exactly the kind of control a future field addition can silently outgrow.

### 5.7 Audit logging

Every authentication event, admin action, database/group/integration/report CRUD action, AI
query, RCA request, and authorization denial is recorded with actor, action, target,
timestamp, success/failure, and IP address. Audit events are themselves append-only from the
application's perspective (no update/delete endpoint exists for `audit_events`) - a full
tamper-evidence guarantee would need database-level write-once enforcement or a separate
audit-log shipping pipeline, neither of which exists yet (see Sec 6).

### 5.8 Transport security, CORS, and headers (Phase 24, headers new)

CORS is restricted to an explicit allow-list (`CORS_ALLOWED_ORIGINS`), never a wildcard
origin, with credentials enabled (browsers reject wildcard-origin + credentials combinations
by spec, so this is not just policy but browser-enforced). `SecurityHeadersMiddleware` adds
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a restrictive `Referrer-Policy`,
and a `Permissions-Policy` denying geolocation/microphone/camera to every response; `Strict-
Transport-Security` is added when `ENVIRONMENT=production`. TLS termination itself is a
deployment concern (nginx/reverse proxy in front of this API) - the application does not
terminate TLS itself.

### 5.9 Rate limiting and resource exhaustion (Phase 24, new)

- A blanket per-client-IP floor (`API_RATE_LIMIT_PER_MINUTE`, default 300/min) on every
  `/api/*` request except health checks.
- Tighter, per-authenticated-user limits on the platform's most expensive operations: AI
  Copilot ask and RCA (10/min each - these cost real LLM inference), report generation
  (20/min), bulk database onboarding (5/min - each call can write up to 100 rows), and
  integration test-delivery (10/min - each call makes a real outbound network request).
- `BodySizeLimitMiddleware` rejects a request exceeding `MAX_REQUEST_BODY_BYTES` (default
  5MB). **Closed, Enterprise Readiness Plan C.2**: a fast pre-check still rejects an
  honestly-declared oversized `Content-Length` immediately, without reading anything, but
  the middleware is now a raw ASGI wrapper around `receive()` that counts actual streamed
  bytes as they arrive and rejects once the *real* total - not the declared header - exceeds
  the limit. Closes exactly the gap this section used to name: chunked Transfer-Encoding
  (no `Content-Length` header at all) or a dishonest, understated one no longer bypass it.
  Verified with a real chunked-encoded ~6MB request against the live app, not just a unit
  test (`backend/tests/test_body_size_middleware.py`).

### 5.10 AI safety

Unchanged from Phase 8/18/19/20 and reviewed fresh in this phase: the tool layer is a fixed,
read-only allowlist with no code path to an Oracle connection or arbitrary SQL; tool
selection is deterministic (caller-provided `database_id`/`incident_id`), not model-driven,
so there is no function-calling-injection surface to defend against yet; the `question` field
is length-capped (2000 chars) at the schema layer; every AI Copilot/RCA call is now also
rate-limited (Sec 5.9, new this phase) since each call costs real inference time/cost.

## 6. Known gaps (as of Phase 27 - Adversarial QA)

Phase 27 ran five adversarial audits against this codebase (SQL injection, RBAC bypass,
credential leakage, AI prompt injection, SSRF bypass) - see the Master Plan Phase 27
compliance report for the full findings. Two real, previously-unfixed issues were found and
fixed (the `RequestValidationError` secret-leak in Sec 5.6, and the send-time SSRF re-check
in Sec 5.4); one real gap was found and documented rather than fixed (multi-tenant isolation,
Sec 1/4.2); one existing defense was confirmed adequate but under-tested (AI prompt-injection
framing - a regression test was added). Remaining gaps:

- No route-inventory lint/test mechanically proves every new endpoint has a permission
  dependency - the pattern is consistent today (Phase 27 verified all 47 current routes) but
  relies on discipline, not tooling.
- SSRF protection covers `Integration.target_url` only - Oracle host/port and SMTP host are
  not checked, though both are inherently admin-configured (same trust level) and, unlike a
  webhook, don't cause the platform to make a request to attacker-influenced content.
- ~~DNS-rebinding protection is narrowed but not eliminated~~ - **closed, Enterprise
  Readiness Plan C.1**: socket-level IP pinning (`app/security/ssrf_transport.py`) now backs
  webhook delivery - see Sec 5.4.
- ~~Multi-tenant isolation does not exist~~ - **decided, not a gap, Enterprise Readiness Plan
  C.3 (2026-09-04)**: single-organization-per-deployment is the ratified deployment model,
  permanently, not a stopgap - see Sec 4.2. `organization_id` + query-scoping work is
  explicitly not planned.
- ~~`BodySizeLimitMiddleware` is a `Content-Length` header check, not a streamed byte-count
  enforcement~~ - **closed, Enterprise Readiness Plan C.2** - see Sec 5.9.
- No content-security-policy for the frontend (a separate static Vite/nginx deployment, not
  served by this backend) - a deployment-level recommendation, not implemented here.
- Audit log is not cryptographically tamper-evident (no hash chaining / external log
  shipping).
- Prompt-injection payloads were tested against the *framing* (the fixed system-prompt text
  and a regression test asserting it survives adversarial session/module/machine content
  reaching the message list) but never against a *real* model - no local LLM server exists in
  this environment (a standing gap across every AI-touching phase; see `docs/RUNBOOK.md`).
- No rate limiting on GraphQL-style N+1-shaped abuse (not applicable - this is a REST API
  with no query-shape-dependent cost variance beyond what pagination limits already bound).
- Scale/load testing (master plan Phase 25) has not validated these limits (300/min global,
  the tighter per-route ones) against realistic concurrent fleet-sized traffic - the defaults
  are reasoned estimates, not load-tested numbers.
- No dedicated chaos-engineering or red-team-against-a-live-environment pass (killing real
  processes under real concurrent load, fuzzing with a real Oracle/SMTP/LLM in the loop) -
  this phase's adversarial testing was static/code-level, the same environment constraint as
  every other phase (no real Oracle/SMTP/LLM/Postgres/Redis available here - see
  `docs/RUNBOOK.md`).
