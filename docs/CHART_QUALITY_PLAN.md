# Chart Quality Plan

Status: **complete** - `CH.1` through `CH.3` all shipped, tested, and live-verified. Triggered
by direct user feedback: "not happy how graphs look,"
narrowed to two concerns - spikes disappearing at wider time ranges (the bigger one) and the
Wait Analysis color palette. Same phased, in-code, live-verified discipline as
`docs/UI_UX_REDESIGN_PLAN.md` - plan the change, implement, test, verify live against the
real running stack in both themes, log, commit - but pre-approved as a batch: phases below
are executed in sequence without a per-phase go-ahead.

## QA analysis (root cause, checked against the real system, not assumed)

**Is data actually lost at wide zoom, or just hard to see?** Checked directly against the
real database: over the last 24h, the real table holds 4,406 raw `instance_health_snapshots`
rows with a genuine max `active_session_count` of 12 (from an earlier deliberate load test).
`recent_instance_health`'s existing even-sampling (`_even_sample`, `UI.5`) returns 1,000
evenly-strided rows for that window, and its max is 11 - **the data survives even-sampling
almost exactly intact** (11 of 12, not silently dropped to the baseline). So this is
overwhelmingly a **rendering** problem, not a backend data-loss bug: a 10-15 minute spike
among 24-168 hours occupies only a handful of pixels on a ~600px-wide chart, with nothing in
the chart's design drawing the eye to it.

**Current `TrendChart.tsx` (shared by Performance and Wait Analysis) critiqued directly,
live, in both themes:**
1. A real spike renders, but as a thin, undecorated sliver - same fill, same opacity, no
   peak emphasis, no reference to what happened at that moment. Confirmed live: an 11x
   baseline spike from a real load test was barely noticeable at the 24h zoom.
2. No connection to the anomalies this exact database already has, timestamped, in
   Postgres: `PerformancePage.tsx` fetches them for the list below the chart but never plots
   them on the chart itself; `WaitAnalysisPage.tsx` does not fetch anomalies at all today
   (grepped - zero matches). A DBA scanning the trend has no on-chart cue for "this is where
   something was flagged."
3. Wait Analysis's `PALETTE` (`TrendChart.tsx:24-33`) is 8 hand-picked Tailwind "600" hues
   (cyan/violet/pink/indigo/teal/fuchsia/lime/sky) - never run through any colorblind-safety
   or perceptual-separation check. Two concrete problems found on inspection, not just "too
   busy": (a) `lime` sits close in hue to this app's own `status.good` green, risking exactly
   the confusion the code's own comment says it's trying to avoid; (b) Oracle's real non-Idle
   wait-class roster (Administrative, Application, Commit, Concurrency, Configuration,
   Network, Other, Scheduler, System I/O, User I/O - confirmed via `telemetry_probe.py` and
   live data) is **10 classes**, not 8 - the current palette silently cycles and reuses hues
   for the last two (alphabetically: System I/O and User I/O reuse Administrative's and
   Application's colors). Documented here as a known limitation, not solved this pass -
   fixing it means either 10 validated hues (harder to keep CVD-safe) or a deliberate overflow
   treatment, and neither is what's bugging the user most.
4. The chart is otherwise unstyled default Recharts: thin dashed gridlines, flat-opacity
   fill, no typographic identity - reads as library defaults with colors swapped in.

## Phases

- [x] **CH.1 - Peak-preserving rendering.** When a series has far more points than the chart
  can usefully render, bucket client-side and plot each bucket's **max** (not an average that
  would smooth a real spike away), so a brief spike is guaranteed a visible peak at any zoom
  level - the same reason real monitoring tools (Grafana et al.) default to max-aggregation
  for zoomed-out threshold-style views, which is exactly this app's use case. No backend
  change: works on whatever `recent_instance_health`/`recent_wait_rounds` already return.
- [x] **CH.2 - Real anomaly markers on the chart.** Plot real, already-detected anomalies as
  reference markers directly on `TrendChart`, colored by their real severity
  (`lib/severity.ts`, already themed for dark mode) - reusing `PerformancePage`'s existing
  anomalies fetch, and adding the equivalent fetch to `WaitAnalysisPage` (which has none
  today).
- [x] **CH.3 - Validated color palette + visual polish.** Replace the hand-picked 8-hue
  `PALETTE` with a set run through the dataviz skill's actual validator (`validate_palette.js`)
  - not eyeballed - fixing the real `lime`/`status.good` collision, and tighten the chart's
  grid/fill/typography to read as deliberately designed.

## Log

**CH.1 (2026-09-08).** Added `bucketToPeaks(data, xKey, seriesKeys, maxPoints)` to
`TrendChart.tsx` (pure, exported, directly tested - same pattern as `assignSeriesColors`):
when a series has more than `maxPoints` (default 400) points, groups consecutive points into
buckets and keeps each bucket's real **max** per series, never an average. Applied inside
`TrendChart` itself so both Performance and Wait Analysis get it for free. A visible caption
("Showing peak values per interval...") appears only when bucketing actually engaged, so a
DBA never mistakes a peak-aggregated view for a raw one. Chose `maxPoints=400` (not the
initially-tried 200) after confirming live that 200 triggered bucketing even for the 1h view
(~240 samples) where full raw resolution renders fine and is more informative -
400 comfortably clears 1h while still engaging for 6h+ (1,000+ samples).

Tests: 5 new direct tests for `bucketToPeaks`, including one reproducing the exact
"brief spike buried in 1000 quiet samples" shape confirmed live against the real database.
Full suite: 229 passed (up from 224); `tsc --noEmit`/`eslint src/` clean.

Live-verified against the real running stack, both themes: at 7d zoom, confirmed via the
**rendered SVG path geometry** (not just a screenshot) that the peak pixel maps to a value of
~11.0 - exactly matching the real database max (11 of a true 12, already only 1 unit off from
`_even_sample`'s own stride, confirmed separately by querying Postgres directly) - proving
zero further dilution from bucketing. Dark mode additionally showed a second, more recent
real spike clearly, plus visible texture from many smaller real anomalies across the week -
a dramatically clearer picture than before the fix.

**CH.2 (2026-09-08).** Added `TrendChartAnnotation`/`matchAnnotationsToChart` to
`TrendChart.tsx`: each annotation (a real anomaly's `detected_at` + severity) is matched to
whichever rendered chart point (post-bucketing) is closest in time, using a `__rawTime` field
each page now includes alongside its display-formatted `xKey`; multiple anomalies landing on
the same point collapse to one (the caller sorts worst-first, so the most severe wins).
Rendered as a `ReferenceLine` colored by severity. `WaitAnalysisPage.tsx` gained an anomalies
fetch it never had before (same query key `PerformancePage` already uses, so the two pages
share cache).

**Real bug caught by live data, not by review**: the first version labeled every annotation
with its severity text permanently on the plot. Against this session's own real, busy anomaly
history (many deliberate load tests earlier today), this produced dozens of overlapping,
unreadable text labels - confirmed live, not hypothetical. Fixed two ways: (1) filtered chart
annotations to HIGH/CRITICAL only on both pages (routine MEDIUM/LOW anomalies stay in the
existing list below the chart, nothing is lost, just decluttered from the plot itself); (2)
dropped the permanent text label entirely - the colored dashed line plus the adjacent list's
own text is the signal, not labels competing for space on the chart.

Tests: 4 new direct tests for `matchAnnotationsToChart` (nearest-point matching, empty chart,
same-point collapse keeping the caller's first/most-severe, unparseable timestamp skipped);
extended `WaitAnalysisPage.test.tsx`'s mock with an `anomalies` case (same "narrow mock,
don't retrofit every old test" convention `UI.6` established) rather than leaving a silent
per-test fetch rejection. Full suite: 233 passed (up from 229); `tsc --noEmit`/`eslint src/`
clean.

Live-verified against the real running stack, both themes, both pages: Performance's 7d
chart showed clean red dashed lines at real CRITICAL moments with no clutter; Wait Analysis
(previously blind to anomalies entirely) now shows the same real markers. A genuinely dense
recent cluster of CRITICAL lines (from this session's own repeated deliberate load testing)
still renders as a tight red band rather than individually spaced lines - an honest reflection
of real, unusually concentrated test activity, not a rendering bug, and not something to
paper over with an arbitrary cap that would hide genuinely recent critical history.

**CH.3 (2026-09-08).** Loaded the `dataviz` skill before touching any color, per its own
procedure ("color comes last... never eyeball a palette, run the validator"). Replaced
`TrendChart.tsx`'s hand-picked 8-hue `PALETTE` (cyan/violet/pink/indigo/teal/fuchsia/lime/sky
- unvalidated, and `lime` genuinely close to `status.good`) with a set actually run through
`validate_palette.js`: magenta/yellow/teal/violet/cyan/orange/blue/pink, in the one ordering
(out of 40,320 brute-forced via the validator's own exported `validate()` function - manual
reordering by hand kept fixing one adjacent pair while breaking another) that clears every
hard gate in **both** light and dark mode - worst adjacent CVD ΔE 15.9 light / 8.2 dark (target
8.0), worst normal-vision ΔE 19.6 light / 15.3 dark (floor 15.0). Dark-mode hex values needed
their own iteration too: reused Tailwind's own light-step hex for teal/cyan at first, which
read too light for the dark lightness band (L 0.70-0.80 vs the 0.48-0.67 ceiling) - stepped
darker until they cleared it.

Also applied the skill's own mark specs, previously never checked against: gridlines were
dashed (`strokeDasharray="3 3"`) where the spec calls for solid hairlines - switched to solid,
horizontal-only (`vertical={false}`, a common restrained-monitoring convention, also cuts
visual noise); area fill was `fillOpacity={0.75}` against a spec of "~10% - a wash, never a
saturated block" - dropped to `0.12`; line stroke width made explicit at 2px (was unset,
relying on the Recharts default).

Tests: existing `assignSeriesColors` tests needed no changes (they check hex-format and
stability, not specific values) - full suite: 233 passed, unchanged from CH.2; `tsc --noEmit`/
`eslint src/` clean.

Live-verified against the real running stack, both themes: Wait Analysis's legend now shows
the validated palette clearly, including a real ~70cs/s "Application" wait-class spike that
stayed sharply visible (proving CH.1's bucketing and the new fill opacity work together, not
just individually); gridlines confirmed solid and horizontal-only in both themes; dark mode
confirmed correct once an explicit "light" theme choice left over from the theme-toggle work
was cleared from localStorage (the manual override correctly took precedence over the OS/
emulated preference - expected behavior, not a bug).

This closes every item on `CHART_QUALITY_PLAN.md`'s checklist (`CH.1`-`CH.3`).
