# OCPCC UI/UX Redesign Plan

Status: **complete** - `UI.1` through `UI.9` all shipped, tested, and live-verified; see each
phase's dated log entry in section 4 for what was actually built (and corrected from this
draft's original assumptions along the way).

## Why this document exists, and how it's built

The first draft of this plan was modeled on Oracle Enterprise Manager's actual Performance
Hub / ASH Analytics feature set - "AAS", "ASH Analytics", "SQL Monitoring", "Generate AWR
Report", ADDM, a full RAC drill-down. That's a mismatch: this product's entire value
proposition is license-tier parity *without* the Diagnostics Pack or Tuning Pack that ASH,
ADDM, AWR reports, and Real-Time SQL Monitoring all require. This version keeps what OEM gets
right at the *behavioral* level - a time-range selector feeding one trend chart, wait-class
breakdowns, a two-table SQL/session drill-down, a graphical execution plan, dimensional
filtering - because those conventions are exactly what decades of Oracle DBAs already know how
to read, and familiarity there is a real adoption advantage. It drops everything that only
works because OEM has ASH/AWR/RAC data behind it, and grounds every screen in what this
product's own collectors actually capture (verified against the code, table in the next
section).

**No Figma.** There's no design/dev handoff on this project - the same person (me, working
with you) writes the design and the code in the same pass, and every screen already has a
real running backend behind it. A static mockup would use fake data; this app already has
real data, and designing against the fake version is how the `formatBytes` bug below would
have shipped invisibly. Every phase in section 4 follows the same loop already proven this
session: **plan the change -> implement it -> write a test that fails red against the old
behavior -> verify live against the real running stack via screenshots -> update this doc ->
commit.** No parallel design file to keep in sync.

## 1. What's real, verified against the code (not assumed)

| Real and available | Not available - out of scope |
|---|---|
| Session sampling: `sid, serial#, username, sql_id, event, wait_class, status, blocking_session, machine, program, module, action` ([telemetry_probe.py](../backend/app/oracle/telemetry_probe.py)) | ASH (`v$active_session_history`) - explicitly not reproduced, per that file's own docstring |
| Full SQL execution plan tree: `operation, options, object_name, object_type, cost, cardinality, bytes, parent_id, depth` at `(sql_id, plan_hash_value)` grain ([sql_probe.py](../backend/app/oracle/sql_probe.py), rendered today in [SqlIntelligencePage.tsx:390-408](../frontend/src/pages/SqlIntelligencePage.tsx)) | RAC: no `inst_id`, `gv$`, or any RAC collector anywhere in the codebase |
| Per-metric, per-hour-of-day rolling baseline (Welford mean/stddev), already returned on every anomaly via the API (`baseline_mean`/`baseline_stddev`, [databases.py:369](../backend/app/api/routes/databases.py)), with a 4-level LOW/MEDIUM/HIGH/CRITICAL z-score classifier ([baseline_engine.py](../backend/app/analytics/baseline_engine.py)) | A generic "baseline for any metric/hour regardless of whether it's currently anomalous" endpoint - only anomaly-attached baseline data exists today |
| A shared `Table` component with `overflow-x-auto` already wired ([Table.tsx:18](../frontend/src/components/ui/Table.tsx)), a shared `Badge` component with 5 tones ([Badge.tsx](../frontend/src/components/ui/Badge.tsx)), `font-mono` already applied to all SQL text, Tailwind already configured with `darkMode: "media"` | Any `dark:` variant classes anywhere in the codebase - the config flag is set, zero components use it |
| Tablespace capacity + growth-rate projection, config-parameter drift, blocking-session chains (`blocking_session` field, currently rendered as a flat message), incident correlation, AI Copilot | Service name, PDB, consumer group as filterable dimensions - not captured |

**Correcting my own earlier live-audit claims, checked against the actual code before writing
this plan:** I originally flagged "tables silently clip their rightmost column" and "SQL
renders in the wrong font" and "severity is a chip in some places, plain text in others" as
three separate bugs. Reading the code shows two of those aren't real: the `Table` component's
`overflow-x-auto` wrapper is working as intended (a real but much smaller polish gap -
there's no visual fade/shadow hinting that more columns exist, just a thin native scrollbar);
and SQL text already carries `font-mono` everywhere, confirmed by zooming into a live
screenshot - IBM Plex Mono is rendering correctly. Severity *is* consistently a `Badge`
component everywhere (Capacity, Incidents, Health, Databases all use it) - but reading
`CapacityPage.tsx:26-32`'s `SEVERITY_TONE` map found the real, precise bug: `HIGH` and
`CRITICAL` both map to the same `"crit"` tone, so they're not merely "hard to tell apart" -
they render as literally identical badges, and this map is duplicated (not shared) between
`CapacityPage` and `IncidentsPage`. Two more real, confirmed bugs: `formatBytes`
([CapacityPage.tsx:40-46](../frontend/src/pages/CapacityPage.tsx)) checks `bytes >= 1024**2`
etc. - comparisons that are never true for a negative number, so a *shrinking* tablespace
(realistic for TEMP/UNDO) falls through to the unformatted `` `${bytes} B` `` fallback,
which is exactly the `-11363580.403086632 B` seen live; and the Execution Plan tree
([SqlIntelligencePage.tsx:400-408](../frontend/src/pages/SqlIntelligencePage.tsx)) renders
`cost` as a plain number with zero highlighting today - no color coding exists at all yet, so
this section of the original plan is 100% new work, not an upgrade.

## 2. Content decisions carried over from the discussion (unchanged)

- **Trend chart**, renamed **Workload Trend** (not "AAS") - stacked-area, wait-class buckets,
  hover shows the collector timestamp and per-class centiseconds, never a sample count that
  implies 1-second ASH cadence at a ~15s collector interval. Context menu offers "Generate
  Report" and "Ask AI Copilot about this window" (both real capabilities) - not "Generate AWR
  Report" / "Open ASH Report" (neither exists).
- **Time control bar**: keep the existing `15m/1h/6h/24h/7d` button-tabs (already built,
  fewer clicks than a dropdown), add the one real gap - a **Custom range** option.
- **Dimensional filtering**: Wait Class, Wait Event, SQL ID, Module, Action, Username,
  Machine/Program - all real captured fields. Service/PDB/Consumer Group/RAC dropped.
- **Wait Analysis page**: upgrade from a single point-in-time bar chart to the same
  time-range-driven stacked-area chart as Workload Trend (reusing one `TrendChart`
  component), since it already samples the same `v$system_event` data every interval.
- **Top SQL / Top Sessions two-table pattern** below the trend chart, columns matching what
  `sql_repo` actually computes (Executions Δ, Elapsed Δ, CPU Δ) - not invented AAS/DB-Time%
  figures.
- **Execution Plan tree**: keep the existing text-tree structure, add relative-cost
  highlighting (a plan line in the top 10% of cost among that plan's own lines = `warning`,
  top 3% = `critical` - relative, not a fixed number, so it generalizes across differently
  sized databases).
- **Blocking Sessions**: upgrade from today's flat "No blocking sessions" state to a
  blocker->waiters tree when a chain exists, built from the already-captured
  `blocking_session` field - no new collector needed.
- **Compare Mode**: "current vs. this database's own mean ± stddev for this hour-of-day,
  n samples" using the real Welford baseline and 4-level classifier - not a naive "vs.
  yesterday same clock time."
- **Investigation Drawer**: a persistent 360px panel carrying filter/time-range context
  across navigation, "Next actions" linking to real capabilities only (Open Incident, Ask AI
  Copilot with context pre-filled, Generate Report - "Run ADDM" dropped).
- **Dropped entirely**: ASH Analytics naming, "AAS", AWR/ASH report generation, ADDM, RAC
  screens, Real-Time SQL Monitoring tab, Service/PDB/Consumer Group/User Session as
  dimensions.

## 3. Real gaps worth fixing that have nothing to do with ASH/RAC

Confirmed live and in code, independent of everything above:

1. `formatBytes` mishandles negative numbers (shrinking tablespaces render an unrounded raw
   float).
2. `HIGH` and `CRITICAL` share one badge tone, and the severity-tone map is duplicated
   per-page instead of shared.
3. `darkMode: "media"` is configured in `tailwind.config.ts` but zero components anywhere use
   a `dark:` class - the flag does nothing today.
4. The top nav has no search, manual refresh, or notifications affordance - just an avatar
   and name.
5. `Activity` sits in the sidebar visually identical to working pages despite being an
   intentional, not-yet-built placeholder.
6. Table overflow works (`overflow-x-auto`), but there's no visual hint (fade/shadow at the
   clipped edge) that more columns exist beyond a thin native scrollbar.

## 4. Execution plan - in code, phased, live-verified

No Figma, no static mockups. Each phase below follows the loop from the intro. Ordered so
later phases build on primitives the earlier ones fix or introduce, and so the two purely
corrective phases (which touch the same `Badge`/token surface as dark mode) land before dark
mode is themed once, not twice.

- [x] **UI.1 - Correctness fixes.** Fixed `formatBytes` to bucket on `Math.abs(bytes)` and
  round the byte-level fallback; extracted the duplicated `SEVERITY_TONE` maps (found in
  three places - `CapacityPage`, `IncidentsPage`, and `PerformancePage`, not just two) into
  one shared `frontend/src/lib/severity.ts`; added a solid-fill `"critical"` tone to `Badge`
  distinct from the existing soft-fill `"crit"`, so `CRITICAL` now reads as a step above
  `HIGH` instead of rendering identically.

  **Log (2026-09-07).** Root cause for `formatBytes`: its `bytes >= 1024**N` threshold checks
  are never true for a negative number, so a shrinking tablespace fell through to the
  unformatted `` `${bytes} B` `` fallback - fixed by bucketing on `Math.abs(bytes)` and
  preserving the sign, plus rounding the byte-level fallback (it previously returned decimals
  verbatim for positive sub-KB values too). Root cause for the severity collision: three
  independent, byte-for-byte-identical `SEVERITY_TONE` consts all mapped `HIGH` and
  `CRITICAL` to the same `"crit"` `Badge` tone - not merely "hard to tell apart," literally
  the same rendered class. Extracted to `lib/severity.ts`; `CRITICAL` now maps to a new
  `"critical"` tone (`bg-status-crit text-white`, solid) while `HIGH` keeps the existing
  `"crit"` (`bg-red-50`, soft tint). `Badge`'s `Tone` type is now exported so `severity.ts`
  can reference it. Deliberately no `dark:` classes added on the new `"critical"` tone - `UI.2`
  themes the whole tone set once dark mode work starts, not twice.

  Tests added: `lib/bytes.test.ts` (KB/MB/GB boundaries, zero, null, and the exact
  live-observed negative float, reproduced as a literal test case), `lib/severity.test.ts`
  (all 5 severities mapped, explicit `HIGH !== CRITICAL` assertion), `components/ui/Badge.test.tsx`
  (all 6 tones render, `"critical"` vs `"crit"` produce different classes). Both new
  regression tests (`bytes.test.ts`'s negative case, `severity.test.ts`'s inequality
  assertion) were confirmed to fail red against the pre-fix code before the fix was applied,
  then pass green after - same discipline as F.1/F.2. Full suite: 145 passed (up from 131),
  `tsc --noEmit` clean, `eslint src/` clean (0 errors, 3 pre-existing warnings in unrelated
  files).

  Live-verified against the real running stack (after a `docker compose restart frontend` -
  the same Windows-bind-mount HMR gap seen earlier this session meant the dev server kept
  serving the pre-fix `CapacityPage.tsx` until restarted): `/capacity`'s real `UNDOTBS1` row
  now shows `-8.9 MB` instead of the raw float; `/capacity`'s alert badges show a clear solid
  dark-red vs. soft-pink distinction between the two severities present on real open alerts;
  `/incidents`'s real CRITICAL rows render as solid dark-red badges; `/performance`'s real
  anomaly list shows `MEDIUM` (soft amber), `HIGH` (soft pink), and `CRITICAL` (solid dark
  red) all visibly distinct in the same list.

  **Correction to this document's own earlier claims**, found while grounding this fix in the
  code rather than assuming: the original live audit said tables silently clip their
  rightmost column and SQL renders in the wrong font - neither held up. `Table`'s
  `overflow-x-auto` wrapper works as intended (the real, smaller gap is no visual affordance
  hinting more columns exist), and SQL text already carries `font-mono` everywhere, confirmed
  by zooming into a live screenshot. Both corrected in section 1 above before this phase
  started, rather than left to be discovered mid-implementation.

- [x] **UI.2 - Dark mode.** Step A and Step B both done (see logs). Add `dark:` variants to
  the shared primitives first (`Table`, `Badge`,
  `Alert`, form controls, page shell/sidebar/top nav), themed against the tokens already
  centralized in UI.1, then sweep the remaining pages. Live-verify via
  `resize_window({ colorScheme: "dark" })` across at least Overview, Performance, Incidents,
  Capacity, and Administration.

  **Log (2026-09-07) - Step A.** An Explore agent inventoried the actual scope before writing
  the plan: 12 shared primitives, a 4-file app shell, ~30 page files, and 2 Recharts pages
  with hardcoded hex series colors (SVG `stroke`/`fill` props - `dark:` classes can't reach
  those, since Recharts needs an actual color string). Added `dark:` variants to all 12
  `components/ui/` primitives (`Table`, `Badge`, `Alert`, `Button`, `Input`, `Select`,
  `Loading`, `EmptyState`, `ErrorState`, `Drawer`, `Modal`, `Tabs` - `Toast` reviewed and left
  unchanged, its solid-fill white-text chips already read correctly on either background) and
  the 4-file app shell (`AppShell`, `Sidebar`, `Header`, `UserMenu`), following one
  translation table (page canvas `bg-slate-50`→`dark:bg-slate-950`, card/shell
  `bg-white`→`dark:bg-slate-900`, nested inset `bg-slate-50`/`100`→`dark:bg-slate-800`, a
  slate text-shade mirror, `border-slate-200`→`dark:border-slate-800`). `Alert`/`Badge` tones
  bypass the static `status.*` hex tokens for their dark variant (those aren't theme-aware)
  and use native Tailwind hues directly instead - kept `Badge`'s `critical` tone (from `UI.1`)
  brighter than `crit`'s dark tint, preserving the same "solid reads a step above soft tint"
  relationship. Added `frontend/src/lib/useIsDarkMode.ts` (a `matchMedia` hook with a change
  listener) and wired it into `PerformancePage`/`WaitAnalysisPage` to pick dark-aware
  grid/axis/series colors for their Recharts charts - neither chart had an explicit grid/axis
  color before this (both relied on Recharts' library-default gray), so this also fixes
  grid/axis visibility against a dark background, not just the series line/bar. Also fixed
  `index.css`'s hardcoded `color-scheme: light` (native form controls/scrollbars were forced
  light regardless of OS preference) and gave the `body`/universal-`border` defaults their
  dark counterparts.

  Adding the hook surfaced a real, unrelated test-infra gap: `jsdom` doesn't implement
  `window.matchMedia` at all, so every existing test for a page that now calls
  `useIsDarkMode()` (`PerformancePage`, `WaitAnalysisPage`) started failing with `TypeError:
  window.matchMedia is not a function` - not a bug in the hook, a missing test-environment
  stub. Fixed once, globally, in `src/test/setup.ts` (defaults to "light, no match" unless a
  test explicitly stubs it, as `useIsDarkMode.test.ts` does) rather than per test file.

  Tests added: `lib/useIsDarkMode.test.ts` (initial value both ways, updates on a mocked
  change event), extended `Badge.test.tsx` with a `dark:` class-presence assertion per tone.
  Full suite: 154 passed (up from 145), `tsc --noEmit` and `eslint src/` clean.

  Live-verified via `resize_window({ colorScheme: "dark" })` (after a `docker compose restart
  frontend` - same HMR gap as `UI.1`) across Overview, Incidents, Capacity, and
  Administration, then back to `"light"` to confirm no regression there. Shell chrome
  (sidebar, header, user menu) and every primitive-built page (Incidents, Administration) now
  render fully and correctly in dark mode with no further work. Confirmed two things
  precisely *not* yet fixed, as expected for a Step-A-only pass: page-level cards built with a
  raw `bg-white` div rather than a shared primitive (e.g. Overview's database card,
  Performance's chart card) are still light - by design, that's Step B; and Capacity's/
  Administration's own table cell text (tablespace names, user emails) is set to an explicit
  `text-slate-800`/`900` directly in page markup with no `dark:` pairing yet, so it renders at
  noticeably lower contrast than it should against the now-dark `Table` background - a real,
  moderate-severity item to lead with in Step B, found live rather than guessed.

  **Log (2026-09-07) - Step B.** Swept every raw page-level color class across the 20
  page/panel files the Step-A inventory hadn't touched (all top-level `pages/*.tsx` except
  `AdministrationPage`, `BlockingLocksPage`, `ForbiddenPage`, `PlaceholderPage`, and
  `administration/AuditPanel.tsx`, which needed nothing - already built entirely from
  Step-A-covered primitives), applying the same translation table established in Step A.
  Fixed the two specific contrast bugs Step A's live-verify flagged
  (`CapacityPage`/`administration/UsersPanel.tsx`'s table cell text) and the confirmed
  `bg-amber-50`/`border-status-warn/30` ad-hoc outlier in `IncidentsPage.tsx`'s RCA
  data-completeness note.

  Found one more real bug while sweeping `SqlIntelligencePage.tsx`: a **fourth** copy of the
  `SEVERITY_TONE` map (identical to the three `UI.1` centralized - `CapacityPage`,
  `IncidentsPage`, `PerformancePage`) had been missed, still mapping `HIGH`/`CRITICAL` to the
  same tone. Grepping for `SEVERITY_TONE` specifically during `UI.1` should have caught this;
  it didn't, likely because that grep ran before this file was open in the same session -
  centralized it into the same `lib/severity.ts` import as the other three rather than
  patching it in place, so this is now the *only* copy anywhere in the codebase.

  No test changes needed - this phase is pure Tailwind class additions to existing markup,
  the `SqlIntelligencePage` severity-map fix is covered by `lib/severity.test.ts`'s existing
  assertions once wired through, and `tsc`/`eslint` catch any broken import. Full suite: 154
  passed (unchanged), `tsc --noEmit` clean after also fixing an unrelated pre-existing type
  error in `test/setup.ts`'s `matchMedia` stub (`as MediaQueryList` needed `as unknown as
  MediaQueryList` - a real gap, not introduced by this phase, just first surfaced when `tsc`
  was re-run), `eslint src/` clean (0 errors, same 3 pre-existing warnings).

  Live-verified via `resize_window({ colorScheme: "dark" })` after another `docker compose
  restart frontend`: `/capacity`'s tablespace names and `/administration`'s user names/emails
  now render at full contrast (confirming the Step-A-flagged bug is fixed); `/` (Overview)'s
  database card and `/performance`'s chart card - both previously still light per Step A's own
  notes - now render fully dark, including the tuned Recharts grid/axis colors actually
  visible against a dark card background for the first time; `/sql-intelligence`'s SQL text
  panel renders legibly in dark mode. Confirmed no light-mode regression by toggling back to
  `colorScheme: "light"` on `/capacity` and comparing against the pre-`UI.2` screenshots.

- [x] **UI.3 - Top nav additions.** Global search (jump to a database or incident - SQL ID
  search dropped from scope, no cross-database SQL lookup endpoint exists), manual refresh,
  and a notifications affordance surfacing recent HIGH/CRITICAL incidents - net-new, nothing
  to extend, all built on existing list/get endpoints with zero backend changes.

  **Log (2026-09-07).** Added `GlobalSearch` and `NotificationsBell`
  (`components/layout/`), both fetching real data lazily (`enabled: isOpen`/always-polling at
  the same 20s interval `IncidentsPage` already uses) rather than on every page load. Added a
  manual-refresh icon button to `Header.tsx` calling `queryClient.invalidateQueries()` with no
  key filter - refetches whatever's currently mounted, no new state needed. Since neither
  `DatabasesPage` nor `IncidentsPage` supported opening a specific record by URL before this
  (confirmed by grep - zero `searchParams` usage in `DatabasesPage.tsx`), both gained a
  one-shot deep-link param (`?id=`, `?incident_id=`) that fetches that one record directly via
  its real single-record endpoint (independent of whatever filters are active), opens its
  drawer, then strips the param so it doesn't linger as a sticky filter.

  Caught one real bug before it shipped: all three new icon `<svg>`s used `h-4.5 w-4.5` -
  **not a valid Tailwind class** (the default spacing scale has no half-step between 4 and 5),
  so every icon rendered at 0×0 - confirmed via `getComputedStyle` showing `0px / 0px` on live
  first look, despite the notification badge itself (a plain positioned `span`, not part of
  the collapsed SVG) rendering its correct real count the whole time, which is what made the
  bug non-obvious from a first screenshot alone. Fixed to `h-5 w-5` (20px, matching the icons'
  own `viewBox="0 0 20 20"`) across all three.

  Tests added: `GlobalSearch.test.tsx` (4 - database/incident substring filtering, both
  results' click-to-navigate paths), `NotificationsBell.test.tsx` (4 - badge counts only
  HIGH+CRITICAL not LOW/MEDIUM, most-recent-first ordering, quiet empty state, click-to-
  navigate), extended `IncidentsPage.test.tsx` and `DatabasesPage.test.tsx` with their
  respective deep-link tests. `DatabasesPage.test.tsx` needed a `MemoryRouter` wrapper added
  to its render helper - it had never needed one before this phase, since `DatabasesPage` had
  no router-dependent hooks until `useSearchParams` was added. Full suite: 164 passed (up from
  154), `tsc --noEmit`/`eslint src/` clean.

  Live-verified against the real running stack: notifications bell showed the real fleet's
  actual 9 open CRITICAL incidents (all real anomaly titles, correctly sorted most-recent-
  first); clicking one navigated to `/incidents` with that exact incident's drawer open and
  its real timeline, then confirmed via `window.location.href` that `?incident_id=` was
  stripped; search for "orcl" matched the real registered database, clicking it opened
  `/databases`'s drawer for that real database, `?id=` likewise stripped; manual refresh
  confirmed via `read_network_requests` - a burst of `/api/databases`,
  `/api/databases/{id}/collector-status`, `/api/databases/{id}`, and
  `/api/databases/{id}/groups` all re-fired together. Checked both themes on Overview - no
  light-mode regression, dark mode legible for both new dropdowns from the start.

- [x] **UI.4 - Navigation clarity.** Light sidebar regrouping (Monitoring / Intelligence /
  Response, real routes only, no renamed pages), a muted "not available yet" tag on
  `Activity`. Live-verify the sidebar renders correctly at 1440/1280/1024 widths.

  **Log (2026-09-07).** Added an optional `group?: "Monitoring" | "Intelligence" | "Response"`
  to `NavItem` (`lib/nav.ts`) and tagged all 14 real routes per the grouping this doc's own
  `UI.2` research had already settled on - purely additive, `App.tsx`'s existing
  `isPlaceholder` filter and every other `NAV_ITEMS` consumer untouched. `Sidebar.tsx` now
  groups the permission-filtered item list by `group` (preserving each item's existing order,
  not re-sorting) and renders a muted uppercase section header above each - reusing the same
  `text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500` treatment
  already established elsewhere as this app's "section label" pattern, so no new visual
  language was introduced. `Administration` keeps no group, rendering after a `border-t`
  divider exactly as it read before this phase, just now clearly separated from the three
  named sections above it rather than blending into one flat list.

  `Activity` gets a small "Soon" pill (`title="Not available yet"` for the full phrase on
  hover/assistive tech) - deliberately not the full phrase inline, since it doesn't fit the
  224px-wide sidebar (`w-56`) next to longer labels like "Change Intelligence" without
  wrapping. Confirmed this is a purely visual affordance: `Activity` remains fully clickable
  and still routes to the real `PlaceholderPage` via `isPlaceholder`, unchanged.

  Tests: extended the existing `Sidebar.test.tsx` (previously 3 tests, permission-filtering
  only) with 2 new cases - the three headers render in order with `Administration` last, and
  `Activity`'s link carries the "Soon" tag/title while `Overview`'s does not. Full suite: 166
  passed (up from 164), `tsc --noEmit`/`eslint src/` clean.

  Live-verified via the Browser pane at three explicit widths (1440×900, 1280×900, 1024×900,
  not presets) against the real running stack - all three group headers, the "Soon" tag, and
  the Administration divider render without overlap or truncation at every width. Clicked
  through to `/activity` in dark mode to confirm it's still fully clickable and still shows
  the real "Activity is not available yet" page - no behavior regression from the visual-only
  change. Checked both themes; no light-mode regression.

- [x] **UI.5 - Workload Trend + Wait Analysis.** All three steps done, see logs. Build the
  shared `TrendChart` component (Recharts `AreaChart`, stacked, wait-class series) to replace
  `PerformancePage`'s existing bare `LineChart`; add the Custom range control; upgrade
  `WaitAnalysisPage` from its current point-in-time bar chart onto the same component; wire
  legend-click filtering across the real dimensions in section 2. Tests: chart renders correct
  series for a known fixture; filter-chip state updates both chart and (once `UI.6` lands) the
  SQL/session tables. Live verify against the real running Oracle 19c instance across a couple
  of time windows.

  **Log (2026-09-07) - Step A (backend).** Researched via an Explore agent before designing
  anything: the original mockup's "CPU/User I/O/Wait as portions of active-session-count"
  stacked chart is literally what real Oracle ASH provides (one row per active session per
  second, summing exactly to the session count) - this product doesn't have ASH.
  `active_session_count` (`instance_health_snapshots`) and wait-class rates
  (`wait_snapshots`, raw cumulative `V$SYSTEM_EVENT` counters, no per-session attribution)
  are two real, valid series that cannot be decomposed into each other; stacking them
  together would assert a relationship the data doesn't support. Corrected scope: two
  separate `TrendChart` instances (Performance's single series; Wait Analysis's multiple
  wait-class series stacked among themselves), not one combined chart.

  Confirmed `GET /api/databases/{id}/waits` is a two-round snapshot delta only
  (`TelemetryRepository.latest_two_wait_rounds`) - no time-range query exists over
  `wait_snapshots` anywhere. But the data already streams in every 15s collector tick and
  sits in Postgres with an indexed `sampled_at` column, so a real history endpoint is a small
  additive read reusing the exact aggregation math (`aggregate_by_wait_class`/
  `compute_wait_class_rates`) `/waits` already calls. Added `TelemetryRepository
  .recent_wait_rounds(monitored_database_id, since, until, limit)` and `GET
  /api/databases/{id}/waits/history?hours=&until=&limit=` (new `WaitClassHistoryPoint`
  schema), walking every consecutive round pair in the window instead of only the two most
  recent.

  Found one more real, pre-existing bug while designing this, not before: `GET
  /health-history` takes `hours` but no `until`, and capped at `limit=1000` via `ORDER BY
  sampled_at DESC LIMIT 1000` - for a `7d` window at a 15s tick (~40,320 possible samples),
  it silently returned only the most recent ~4.2 hours, mislabeled as "7 days." Fixed
  together with this phase rather than separately, since an arbitrary custom range (this
  phase's other addition) would hit the exact same silent-truncation immediately. Added
  `_even_sample()` (`telemetry_repo.py`) - when a window's matches exceed `limit`, selects an
  evenly-spaced subset spanning the *whole* window (always keeping the newest and oldest)
  instead of silently keeping only the most recent `limit` - applied to both
  `recent_instance_health` (existing) and the new `recent_wait_rounds`. Also threaded a real
  `until` param onto `/health-history` (the repo method already accepted it; the route just
  never exposed it) - `hours` becomes "hours before `until`," defaulting to now when omitted,
  existing callers unaffected.

  Tests: `test_telemetry_repo.py` (new - 5 pure unit tests for `_even_sample`: within-limit,
  keeps-newest-and-oldest, spans-whole-range-not-just-tail, limit-of-one, empty), extended
  `test_dashboard_api.py` with 3 new tests for `/waits/history` (needs-two-rounds, computes a
  rate per consecutive pair oldest-first, spans the whole window when rounds exceed `limit` -
  this one traced the exact expected `_even_sample` output by hand to assert a precise,
  deterministic timestamp rather than a fuzzy heuristic). All 9 pre-existing
  `test_dashboard_api.py` tests pass unchanged (their fixture data is small enough to never
  hit the even-sampling path at all). Backend suite: 17/17 new+touched tests pass; full
  `pytest` run kicked off in parallel with continuing to Step B, checked before considering
  this phase done.

  Live-verified directly against the real API on the real running stack (after a `docker
  compose restart backend`): `/waits/history?hours=1` returned 229 real points across all 9
  real Oracle wait classes (User I/O, System I/O, Concurrency, Application, Commit,
  Configuration, Administrative, Network, Other) from the live instance, all `0.0` given the
  database's actual current idle state; `/health-history?hours=168` (7 days) now returns
  points spanning `2026-09-03` to `2026-09-07` - the database's *entire* real collection
  history since it was registered ~3.3 days ago, not a truncated recent slice, confirming the
  even-sampling fix; `until=2026-09-07T03:00:00Z&hours=1` correctly bounded the real response
  to exactly `[02:00, 03:00)`. Full backend suite finished afterward: 474/474 passed - no
  regression anywhere from the even-sampling change.

  **Log (2026-09-07) - Steps B/C (frontend).** Built `TrendChart`
  (`components/charts/TrendChart.tsx`) - a shared Recharts `AreaChart` where every caller's
  series share one `stackId`, so a single-series caller (Performance) gets the identical
  visual treatment as a multi-series one (Wait Analysis) without a fabricated stack. Series
  colors come from a fixed 8-hue categorical palette (cyan/violet/pink/indigo/teal/fuchsia/
  lime/sky) deliberately picked to avoid the red/amber/green already used for severity
  elsewhere, assigned by each key's *alphabetically-sorted* position so a wait class keeps its
  color across refetches even as rates reorder by rank. Legend `onClick` toggles that series'
  Recharts `hide` prop (real, supported since well before the pinned `recharts@3.10.1`) - the
  actual, honest "legend-click filtering" this phase can deliver, since there's no SQL/session
  table on either page yet to cross-filter (that's `UI.6`, once those tables exist). Also
  built `TimeRangeControl` (`components/charts/TimeRangeControl.tsx`) - the existing preset
  buttons plus a new **Custom** toggle revealing two `datetime-local` inputs, reusing the
  exact input styling `ReportsPage`'s period-start/end fields already established.

  `WaitAnalysisPage` now calls `/waits/history` and derives its series list from whatever
  wait-class keys are actually present in the response (never a hardcoded list).
  `PerformancePage` swapped its bare `LineChart` for `TrendChart` with one series
  (`active_session_count`) and gained the same `TimeRangeControl`; its anomaly-window filter
  was corrected to bound against the *selected* range's actual `[since, until]` (previously
  always `[now - hours, now]`, which would have been wrong the moment a custom `until` in the
  past was introduced).

  **Correction applied before writing any chart tests**: `WaitAnalysisPage.test.tsx`'s own
  existing comment already documented that Recharts' `ResponsiveContainer` measures 0×0 in
  jsdom (no `ResizeObserver` polyfill), so `<Area>`/`<Legend>` never actually draw in a test.
  Rather than add a polyfill this phase doesn't need, extracted `TrendChart`'s two genuinely
  pure logic pieces - `assignSeriesColors` (8 tests: stable across reordering, light/dark
  differ, wraps the palette past 8 keys) and `toggleHiddenKey` (4 tests: add/remove/no-mutate/
  leaves-others-untouched) - and unit-tested those directly; `TimeRangeControl` has no such
  limitation (plain form elements, not Recharts) and got full interaction tests instead (5 -
  preset click, active-state highlighting, Custom form open/apply/validation). Rewrote
  `WaitAnalysisPage.test.tsx` for the new endpoint shape (a plain array, no `available`
  wrapper) and extended `PerformancePage.test.tsx` with a Custom-range test confirming the
  exact `hours`+`until` query. Full frontend suite: 180 passed (up from 166), `tsc --noEmit`/
  `eslint src/` clean (2 new warnings matching an already-accepted pattern elsewhere in this
  codebase, not a new category of issue).

  Live-verified against the real running stack, both themes: `/wait-analysis` renders a real
  9-series stacked chart from the live Oracle instance (all real wait classes, legend colors
  visibly distinct from severity red/amber elsewhere in the app); clicking "Commit" in the
  legend dimmed it and removed it from the chart, revealing real smaller-magnitude activity
  in other classes that had been masked by it in the stack; `/performance`'s single-series
  chart renders with no legend (correctly gated for `series.length > 1`); Custom range's
  `datetime-local` → UTC `until` conversion confirmed correct via the real network request
  (`until=2026-09-06T22:00:00.000Z` for a `03:00` local-time input, matching the browser's
  own timezone offset) and the resulting empty state for that specific window was the honest,
  correct response, not a bug.

- [x] **UI.6 - SQL Intelligence upgrade.** Two-table Top SQL / Sessions layout (renamed from
  "Top Sessions" - see log, no ranking exists to justify that name); Execution Plan
  relative-cost highlighting. Live-verified against real captured SQL and plans on
  `/sql-intelligence`.

  **Log (2026-09-07).** Researched via an Explore agent before designing anything, and found
  the doc's own wording here was internally inconsistent with the real app structure:
  - **"Under the trend chart"** - `SqlIntelligencePage.tsx` has no chart of any kind (`UI.5`'s
    `TrendChart` is used only by `PerformancePage`/`WaitAnalysisPage`, separate pages). The
    doc's phrasing assumed a single-scrollable-page structure that doesn't match this app's
    real IA (four separate nav items: Performance/Wait Analysis/SQL Intelligence/Sessions).
    Corrected: the two-table pattern stands alone here, no chart involved.
  - **"Confirm/extend the backend's top-sessions ranking"** - there is no ranking to extend,
    not even an unexposed one. `GET /sessions` returns every row from the latest `V$SESSION`
    round with no `ORDER BY` on any ranking column, and the collector doesn't even gather a
    resource-consumption column (no CPU time, no logical reads) to rank by - genuinely adding
    that would be new backend probe/model/schema work this phase doesn't need. Corrected: a
    plainly-named **Sessions** panel (not "Top Sessions" - that name would assert a ranking
    that doesn't exist), sorted ACTIVE-first then by SID (both real fields), capped at 50 rows
    displayed - a practical UI cap, not a claimed rank.
  - **"Wire legend-click filtering across the real dimensions"** - `TopSqlRow` has no
    wait-class/module/username/machine columns at all (it's a per-SQL-statement delta, not a
    per-session fact); only `sql_id` exists on both Top SQL and Sessions. So dimensional
    filtering happens *within* the new Sessions panel (its own real columns), and **SQL ID is
    the one genuine cross-table link** - clicking it in either table opens the same
    `SqlDetailDrawer` that already existed for Top SQL rows, via the same `selectedSqlId`
    state, no new drawer or state needed.

  Execution Plan cost highlighting needed no correction - `PlanLineRow` really did render
  `cost` as a single fixed gray span with zero conditional logic, exactly as the doc claimed,
  even though `SqlPlanLine`'s `cost`/`cardinality`/`bytes` have been fully available all
  along. Added `computeCostTiers(lines)` (pure function, exported for direct testing) -
  97th-percentile-and-up cost among *that plan's own* lines is `critical`, 90th-97th is
  `warning`, relative not fixed (same reasoning as `UI.1`'s severity work), computed once per
  plan in a new `PlanTreeLines` wrapper rather than recomputed per row.

  This phase was **entirely frontend** - the Sessions panel reuses the exact same
  `GET /sessions` endpoint and query key `SessionsPage.tsx` already uses (shares cache
  between the two pages), and the cost data was already being fetched. Drive-by fix: cleaned
  up a duplicated `dark:text-slate-500` class in `StatspackSummary`'s headers (leftover from
  an earlier sweep's `replace_all` matching twice).

  Tests: extended `SqlIntelligencePage.test.tsx` (5 → 11) - Sessions panel renders real
  session rows; clicking a Wait Class value adds a filter chip and narrows the panel to
  matching rows only, removable via the chip's own "×"; clicking a session's SQL ID opens the
  same `SqlDetailDrawer` Top SQL rows already use; three direct `computeCostTiers` tests
  (highest-cost-critical/lowest-untiered, null-cost never tiered, no-cost-anywhere tiers
  nothing). Full suite: 186 passed (up from 180), `tsc --noEmit`/`eslint src/` clean (1 new
  warning matching the same already-accepted pattern as `UI.5`'s `TrendChart.tsx`).

  Live-verified against the real running stack: Sessions panel showed the real live session
  (SID 1699, DBSNMP) with a clickable SQL ID and Wait Class; clicking the SQL ID opened the
  real `SqlDetailDrawer` with real SQL text, real plan history, and real recent-sessions list;
  clicking "Network" in the Wait Class column added a real filter chip, highlighted its
  origin cell, and removing the chip cleared the filter; opened a real plan tree and confirmed
  via computed DOM classes (not just a screenshot) that its one relatively-highest-cost line
  (cost=1 among five cost=0 siblings) rendered with the `critical` tier class while every
  cost=0 line stayed the plain muted gray - proving the *relative* percentile logic works
  correctly even when absolute costs are tiny. Checked both themes; no light-mode regression.

- [x] **UI.7 - Blocking Sessions tree.** Replaced the flat one-row-per-pair table with a
  real blocker->waiters hierarchy, reconstructed client-side from the same flat data. Live-
  verified against a real, deliberately constructed blocking scenario, not a fixture.

  **Log (2026-09-07).** `BlockingLocksPage.tsx`'s flat `Table` (one row per (blocked,
  blocker) pair) was already receiving everything needed for a real multi-level tree - the
  backend's `build_blocking_chains` (`backend/app/telemetry_views.py:16-37`) emits one entry
  per session with its *direct* blocker, so a 3-level chain (A blocks B blocks C) already
  appears as two entries in the same flat list; the tree just wasn't being built from them.
  This phase is **entirely frontend** - no backend or collector change, same reasoning as
  every prior data-already-exists phase.

  Added `buildBlockingTree(chains)` (pure function, exported for direct testing - same
  pattern as `UI.6`'s `computeCostTiers`): groups entries by `blocker_sid`; a root is any
  `blocker_sid` that never itself appears as a `blocked_sid`; recurses into each waiter that
  is itself a blocker elsewhere in the same list, with a `visited` guard against a cyclical
  fixture (real Oracle deadlock detection means a true cycle shouldn't persist in real data,
  but the guard is cheap insurance and is itself unit-tested). Tree rendering reuses `UI.6`'s
  Execution Plan indentation convention (a bordered, indented `<ul>` per level); root gets
  `Badge tone="crit"` ("Blocker") plus a real total-waiter count (including nested waiters),
  each waiter gets `tone="warn"` ("Waiting"); the existing "not in this sample" badge is kept,
  attached only at the root (by construction every non-root node came from a `blocked_sid` in
  the same round, so it is always in-sample).

  Tests: extended `BlockingLocksPage.test.tsx` (2 -> 8) - 5 direct `buildBlockingTree` tests
  (single pair, multiple waiters under one blocker, a genuine 3-level chain nests correctly,
  an out-of-sample blocker is still a valid childless root, a contrived cyclical fixture
  terminates instead of hanging) plus a page test asserting a waiter renders nested inside its
  blocker's own list item, not as a sibling. The existing "not in this sample" test's
  single-string assertion (`"SID 43 (APP_USER)"`) had to change shape, since the tree renders
  SID and username as separate elements - split into two assertions scoped to that waiter's
  own `<li>` (`within(...)`), needed because both the root and the waiter are `APP_USER` in
  that fixture and an unscoped query matched both. Full suite: 192 passed (up from 186);
  `tsc --noEmit` clean; `eslint src/` clean (1 warning, the same already-accepted
  fast-refresh pattern as `UI.5`/`UI.6`'s other exported pure functions).

  Live-verified against a **real** constructed scenario, not a screenshot of a fixture: first
  checked what the real monitoring account (DBSNMP) can actually do rather than assuming -
  it has `CREATE TABLE` but no `EXECUTE` on `DBMS_LOCK` - so the mechanism was a scratch
  table + `SELECT ... FOR UPDATE` row lock, the same "enq: TX - row lock contention" scenario
  a real blocked application session hits. A throwaway script opened two real overlapping
  `python-oracledb` connections (via `build_connection_params`/`SecretProvider`, the app's own
  real decrypted credentials - no fixture), held a row lock on one for ~100s (spanning
  multiple real 15s collector ticks) while a second connection blocked waiting on it, then
  released. `/blocking-locks` rendered the real blocker (SID 729, DBSNMP) with a real waiter
  count of 1, the real waiting session (SID 2061) correctly nested and indented beneath it
  with its real SQL ID - confirmed in both dark and light themes. After releasing the lock
  and letting one more real collector tick run, the page correctly reverted to the honest
  "No blocking sessions" / "Nothing is currently blocked in the latest sample" empty state -
  proving the tree isn't just rendering once but genuinely tracks the live sample. The
  verification scripts were throwaway (deleted after use, never committed) - `docs/` records
  the method, not the script.

- [x] **UI.8 - Compare Mode.** Added the small new backend endpoint this needed (confirmed,
  per open question 2) and a new `CompareModePanel` showing a metric's current value against
  its real rolling baseline, live-verified on `/performance`.

  **Log (2026-09-08).** `GET .../anomalies` only ever attaches `baseline_mean`/
  `baseline_stddev` to already-detected anomalies - a historical snapshot taken at detection
  time (`analytics_service.py:75-77`), not a live join. It structurally can't answer "what's
  the baseline right now, whether or not this is anomalous," confirming section 1's gap-table
  note. Everything else needed already existed: `BaselineRepository.get(database_id,
  metric_key, hour_of_day)` already reads the exact `(database, metric, hour-of-day)` bucket;
  `baseline_engine.py`'s `z_score`/`classify_severity` are the same functions that already
  judge real anomalies. Added `GET /{database_id}/metrics/compare` (new `MetricCompareResponse`
  schema) that calls these directly - Compare Mode's verdict is always identical to what would
  actually open a real incident, never a second classifier. Renamed
  `AnalyticsService._compute_current_metrics` -> `compute_current_metrics` (body unchanged) so
  the route could reuse the same current-value logic (`latest_instance_health` for
  `active_session_count`, `latest_two_wait_rounds` + the already-shared
  `aggregate_by_wait_class`/`compute_wait_class_rates` for wait rates) instead of
  reimplementing it.

  **Real bug caught by a real test, not by review:** `metric_key` was first designed as a
  path segment (`/metrics/{metric_key}/compare`), matching this file's other single-resource
  routes. A test using the real Oracle wait class name **"User I/O"** (confirmed as the real
  discovered-live value in the metric_key universe - see `UI.5`) failed with a 404: Oracle's
  own wait class name contains a literal `/`, which a path segment cannot carry even
  percent-encoded (the ASGI server decodes `%2F` back to `/` before routing sees it, so the
  request lands on a path with too many segments). Fixed by moving `metric_key` to a query
  parameter (`GET /{database_id}/metrics/compare?metric_key=...`) - still consistent with this
  file's existing `hours`/`until`/`limit` query-param convention, and query strings handle an
  embedded `/` correctly.

  `CompareModePanel.tsx` (`components/telemetry/`, alongside `DatabaseSelector.tsx`) exports
  two pure, directly-tested functions: `computeDeviationPosition` (a purely descriptive
  `(observed-mean)/stddev` position on a ±3σ track, shown whenever any baseline exists at all
  - never itself a verdict) and `compareStatus` (maps a real backend severity through the
  already-existing `SEVERITY_TONE`, or reports "Normal"/"Building baseline" for the two null
  cases - the backend's classifier stays the only source of the anomalous/not verdict).
  Wired into `PerformancePage.tsx` for `active_session_count` only this phase - the component
  is fully generic, so extending it to `WaitAnalysisPage` per wait-class is a real, deliberately
  deferred follow-up (it would need its own wait-class-picker UX decision this phase didn't
  need to make).

  Tests: 5 new backend tests (`test_dashboard_api.py`) - honest unavailable before any
  telemetry, a wait-class metric_key not currently reporting (using the real "User I/O" name,
  the same one that caught the routing bug), a current value with no baseline row yet, a
  seeded 20-sample baseline plus a >6σ current value asserting the *exact* real
  `classify_severity` output (proving the route uses the real classifier), and an
  out-of-range `hour_of_day` rejected with 422. Backend suite: 479 passed (up from 474).
  Frontend: direct tests of both pure functions (center/+1σ/clamped-extreme/`stddev<=0`→null;
  all 4 real severities + normal + building-baseline) plus component tests for the
  not-available, building-baseline, and full-comparison states, including one asserting a
  `metric_key` containing `:` and `/` is correctly percent-encoded in the request. Frontend
  suite: 203 passed (up from 192); `tsc --noEmit`/`eslint src/` clean (2 new warnings, the
  same already-accepted fast-refresh pattern as every other file in this redesign that exports
  a pure function alongside its component).

  Live-verified against the real running stack, both themes: `/performance` showed a real
  live comparison - current value 1.0, baseline 1.008 ± 0.089 from 870+ real accumulated
  samples for the real current hour bucket, correctly reading `z ≈ -0.09` as "Normal" (green).
  Confirmed via `javascript_tool` (not just a screenshot) that the deviation marker's computed
  `left: 48.5%` matched the hand-calculated position for that exact live z-score, and that it
  carried the real `bg-status-good` class. Watched the panel's own 15s `refetchInterval` pull
  a fresh, incrementing real sample count (870 -> 871 -> 872) across repeated checks - proving
  it's a live read, not a cached snapshot. No light-mode regression.

- [x] **UI.9 - Investigation Drawer.** A persistent, toggleable 360px rail carrying "the
  database I'm currently looking at" across navigation, with three real next actions. This
  was the final `UI_UX_REDESIGN_PLAN.md` checklist item.

  **Log (2026-09-08).** Checked the doc's own premise before building anything - it claims
  this phase is "wired to the real deep-links established in UI.5-UI.8." Grepped every
  candidate file and the actual UI.5-UI.8 log entries for a context-menu, an "Ask AI about
  this," or a pre-filled-report link. **None exist** - that claim was aspirational, not a
  build record. UI.9 built all three link-establishing mechanisms from scratch:
  - **Open Incident**: confirmed there is no create-incident flow anywhere (no `POST` route,
    `IncidentsPage.tsx` has no create form) - incidents only ever open server-side via
    `IncidentService.correlate_anomaly`. So this action can only mean "navigate to an
    existing, already-correlated incident," reusing the exact `/incidents?incident_id=X`
    deep-link `UI.3` already built (zero `IncidentsPage.tsx` changes needed). Also confirmed
    no route goes from a specific `metric_key`/anomaly to "its" incident - only
    `GET /api/incidents?monitored_database_id=X` exists - so this action is necessarily
    **database-scoped** (newest non-resolved incident), not metric-specific.
  - **Ask AI Copilot / Generate Report**: the backend already accepts `database_id` for both
    (`CopilotAskRequest`, `POST /api/reports`), but neither `AiCopilotPage.tsx` nor
    `ReportsPage.tsx` had any pre-fill entry point - no props, no `useSearchParams`. Added one
    `?database_id=` param each, seeding their existing form state on mount.

  **Architecture**: `AppShell.tsx` renders `<Outlet />` inside itself (not the reverse), so it
  never unmounts between page navigations - a sibling panel placed there genuinely survives
  route changes with no router change needed. New `InvestigationProvider`
  (`lib/investigation-context.tsx`) mounted in `main.tsx` alongside the existing
  `ToastProvider`/`AuthProvider` - the same "context above `BrowserRouter`" precedent both
  already establish. The one piece of context every page genuinely already has in common is
  which database is selected (every page renders the same shared `DatabaseSelector`), so that
  component gained one small effect reporting the current selection into the shared context -
  zero per-page changes needed across the ~10 pages that use it.

  **A deliberate, documented deviation from `AuthContext`/`ToastContext`'s pattern**:
  `InvestigationContext` uses a real no-op default value instead of `undefined` +
  throw-if-missing. `DatabaseSelector` calls this hook and is rendered inside every existing
  page's tests, none of which wrap a provider - a throwing default would have meant editing
  ~10 existing test files for a purely additive feature. A non-throwing default means they all
  keep passing unchanged; only the new tests that actually exercise this feature wrap the real
  `InvestigationProvider`.

  New `InvestigationDrawer.tsx` (`components/layout/`) - not a variant of the existing
  `Drawer.tsx` (used 6 times today for full-screen, backdrop-dimmed record detail): this is a
  non-blocking rail with no backdrop, `z-40` (below `Drawer.tsx`'s `z-50`, so an existing
  per-page detail drawer always renders on top if both are open at once), toggled by one new
  inline `Header.tsx` icon button (matching the existing "Refresh" button's own inline
  style - no new file, since it carries no state of its own).

  Tests: 3 new test files - `investigation-context.test.tsx` (default state, toggle/close,
  explicitly asserts the no-provider case doesn't throw), `DatabaseSelector.test.tsx` (new -
  no test file existed for this component before; asserts it renders standalone without a
  provider, and reports into a real one), `InvestigationDrawer.test.tsx` (closed by default,
  honest empty state, disabled "Open Incident" with zero open incidents, opens the newest
  non-resolved incident by `last_evidence_at`, both pre-fill navigations, and an
  integration-style test proving the real Header icon → context → rail wiring end to end).
  Extended `ReportsPage.test.tsx`/`AiCopilotPage.test.tsx` with `MemoryRouter` (neither had one
  before `useSearchParams` was added - the same gap `DatabasesPage.test.tsx` had before `UI.3`)
  plus one pre-fill test each. Full suite: 217 passed (up from 203); `tsc --noEmit`/
  `eslint src/` clean (1 new warning, the same already-accepted context-file pattern
  `auth-context.tsx`/`Toast.tsx` already carry).

  Live-verified against the real running stack, both themes: opened the rail on `/performance`
  and saw the real selected database and its real open-incident count (10, matching the
  notifications bell's own badge); clicked "Open Incident," landed on the real newest incident
  ("Commit wait time anomaly," CRITICAL) with its real drawer open, closed that drawer, and
  confirmed the rail was **still open** underneath - the actual persistence property, not just
  a visual claim; clicked "Ask AI Copilot" and confirmed the real database dropdown was
  pre-selected; clicked "Generate Report" and confirmed the same in the generate form. A fresh
  page load on `/` (which has no `DatabaseSelector`) showed the honest "No database selected
  yet" empty state. No light-mode regression.

  This closes every item on `UI_UX_REDESIGN_PLAN.md`'s phased checklist (`UI.1`-`UI.9`) and,
  per this doc's own note, closes Enterprise Readiness Plan Gate G2.

Each checked-off phase gets a dated log entry here (root cause / design choice / live
verification method and result) the same way F.1 and F.2 were documented in
`docs/ENTERPRISE_READINESS_PLAN.md`, and completing this initiative closes that plan's Gate
G2 (currently "partial" - "a full control-by-control UI audit hasn't run as its own pass").

## Open questions

1. UI.1 is small, self-contained, and fixes three confirmed real bugs regardless of anything
   else here - reasonable to start immediately without waiting on sign-off for the rest?
2. UI.8 (Compare Mode) may need a small new backend read endpoint, not just a frontend
   change - confirm that's in scope before it's reached.
3. Confirm the UI.4 sidebar regrouping (Monitoring/Intelligence/Response) is acceptable,
   since it's the one place this reorganizes existing navigation rather than only reskinning
   it.
