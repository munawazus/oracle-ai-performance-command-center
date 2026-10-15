# UI Control Inventory — Master Plan Sec 34 ("Every Button Works")

Enterprise Readiness Plan item B.2. Every interactive control across every page was
enumerated, checked for a defined behavior, and checked against existing test coverage.
Where a real, non-trivial gap was found, a test was added in this pass. Where a gap is
lower-value (a filter `Select` never programmatically changed, a secondary button instance
alongside an already-tested one, etc.), it's named explicitly below rather than silently
left out - the goal is an honest inventory, not a clean scorecard.

**Scope**: 25 page files under `frontend/src/pages/`, plus the shared `components/ui/`
controls every page depends on, plus the always-present app shell (`Sidebar`, `UserMenu`).

## What this pass found and fixed

- **`frontend/src/components/ui/StaleState.tsx` was genuinely dead code** - rendered
  nowhere, confirmed by grep across `frontend/src`. Deleted rather than tested, per this
  project's standing convention (delete unused code rather than write a test to justify
  keeping it).
- **`DatabasesPage.tsx` (489 lines, the platform's core CRUD surface) had zero test coverage
  before this pass.** Added `DatabasesPage.test.tsx` (14 tests): list/empty states, group and
  tag filtering, register/edit/delete a database, the edit-form's blank-username/password-
  means-"keep current" logic, group-membership checkbox toggling, and both the page-level
  (Databases/Groups) and drawer-level (Details/Capabilities) tab switches.
- **`IncidentsPage`'s entire `incident:manage` write path (Acknowledge/Assign/Resolve) had
  never been clicked in a test** - only asserted *absent* for an unprivileged user. Added 3
  tests exercising the real happy path for each action, including the assign button's
  disabled-until-a-user-is-picked behavior and the resolve form's optional note.
- **`UsersPanel.tsx` (261 lines, admin-only user/role management) had zero test coverage.**
  Added `UsersPanel.test.tsx` (7 tests): create a user with selected roles, edit an existing
  user's roles, enable/disable toggle in both directions, and canceling create without a
  side effect.
- **`AuditPanel.tsx` had zero test coverage.** Added `AuditPanel.test.tsx` (4 tests): the
  action-prefix filter resets pagination to page 1, and the Previous/Next buttons both fire
  the right request and correctly disable at each boundary.
- **`AdministrationPage.tsx` (the permission-gated tab router for all four admin panels) had
  zero test coverage.** Added `AdministrationPage.test.tsx` (4 tests): the "Nothing to
  administer" fallback when a user has none of the three gating permissions, that only the
  permitted tabs render for a partial-permission user, that all four render for a full-
  permission user, and that clicking a tab actually swaps the mounted panel.
- **No shared-component tests existed for `ErrorState`, `Modal`, or `Drawer`**, despite being
  used on nearly every page in the app (an `ErrorState` "Try again" button alone appears on
  ~18 different query sites). Added `ErrorState.test.tsx` (3 tests: renders as an alert,
  omits the button when no retry handler is given, calls the handler on click),
  `Modal.test.tsx` (5 tests: closed renders nothing, open renders title/children/footer,
  close button and Escape both call `onClose`), and `Drawer.test.tsx` (5 tests: the same
  set, plus that a backdrop click closes it while a click on the panel's own content does
  not).

**115 frontend tests now pass** (was 70) across 27 files (was 20). `tsc -b`, `eslint .`, and
`vite build` all remain clean.

## What's still honestly open

Not fixed in this pass - either lower value (a filter never programmatically exercised, a
second identically-wired button instance) or large enough to warrant its own dedicated pass
rather than folding in here. None of these are known-broken; they are simply untested.

- **Read-only telemetry pages** (`PerformancePage`, `SessionsPage`, `WaitAnalysisPage`,
  `BlockingLocksPage`, `HealthPage`, `OverviewPage`): the shared `DatabaseSelector` control
  and each page's own `ErrorState` retry are rendered and asserted present, but never
  actually changed/clicked in a test - only the default auto-selected-database render path
  is exercised. `PerformancePage`'s five time-range buttons (15m/1h/6h/24h/7d) are the most
  visible instance of this pattern.
- **`ReportsPage`**: the "Report type" `Select`'s five non-default branches (each swaps in
  different sub-fields), the "Download PDF" button (a separate code path from the
  already-tested "Download CSV"), and the per-section `<details>` disclosure are untested.
- **`NotificationsPanel`**: the STARTTLS checkbox, and the comma-separated-email parsing for
  recipients, are never actually exercised (only the loaded display value is asserted).
- **`IntegrationsPanel`**: the "Fires on" severity checkboxes and the "Enabled" checkbox are
  never toggled (form submission always uses their defaults); the edit flow (as opposed to
  create) and the Slack/Teams integration-type branches are untested.
- **`CapabilityPanel`**: "Test Connection" and "Detect Capabilities" are asserted present/
  gated correctly by permission, but never actually clicked - the resulting `Alert` render
  paths are unverified in tests (this page has real integration-level coverage from A.4/A.6's
  live testing against the real Oracle instance, which is a different, real-infra kind of
  evidence than a unit test provides).
- **Secondary button instances**: several pages render the same action (e.g. "New Group",
  "New Integration", "Add Database") twice - once in the page header, once in an
  `EmptyState`'s action slot. Existing and newly-added tests generally click whichever
  instance `getAllByRole(...)[0]` resolves to (the header one); the empty-state instance's
  wiring is the same handler but is not separately exercised.
- **`Modal`'s `aria-labelledby="modal-title"` and `Drawer`'s `aria-labelledby="drawer-title"`
  use a hardcoded DOM id** - two simultaneous instances of either would collide on
  accessible-name lookup. Not currently reachable (the app never opens two at once), but
  worth knowing if that changes.
- **`UserMenu`'s open/close toggle and "Sign out"** are covered only by
  `frontend/e2e/login-flow.spec.ts` (real browser, requires the full stack) - there is no
  `UserMenu.test.tsx` component-level test.
- **`AuditPanel`'s pagination controls only render when the current page has data** - an
  empty result page (e.g. after filtering to a prefix that never had a first page) has no
  visible way back to page 1 other than clearing the filter. Confirmed as designed rather
  than a bug, but worth a product decision if it's ever surprising in practice.

## Maintaining this document

This file is the living summary of control coverage - update it, not just the Enterprise
Readiness Plan's Log entry for B.2, whenever coverage changes materially (a new page ships,
a listed gap gets closed, a new gap is found). The full page-by-page control enumeration this
document was built from is not separately preserved verbatim; re-run the audit against the
current page files if a complete re-enumeration is needed rather than trusting this summary
to have stayed exhaustive as the app evolves.
