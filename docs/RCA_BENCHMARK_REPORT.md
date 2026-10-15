# RCA Benchmark Report — Master Plan Phase 20 (Enterprise Readiness Plan E.1)

Master plan deliverable: a gold-incident set with hallucination/uncertainty scoring for
`RcaService.investigate()` — the one gap `docs/PRODUCTION_CERTIFICATION.md`'s G7 AI gate named
explicitly: *"structured output exists... has never been scored against a benchmark."*

**This report now covers two real runs, not one**: the original 5-scenario/5-check baseline
below (kept verbatim, unmodified), and a second, current run after a real architectural change —
a deterministic evidence/investigation layer (`app/ai/investigation.py`), a two-call RCA split
(a proven 9-field investigative core, plus a separate seeded narrative-expansion call), an
explicit anti-refusal instruction, and two new checks plus a 6th gold scenario
(`stale_evidence`). See **"Re-run after the evidence-governance rework"** near the end for the
honest after-picture — improved on some fronts, unchanged on others, exactly as measured, not as
hoped for.

## Methodology and environment (read this before the numbers)

Unlike every other benchmark in this repo (`docs/SCALE_TEST_REPORT.md`'s load benchmark, which
runs against fake Oracle/queue doubles by necessity — no real Oracle instance, SMTP relay, or
LLM server has ever been reachable from *that* sandboxed run), this benchmark's whole point is
the opposite: it is a **genuine, real network call to a real local model**, not a simulation.
This run used:

- **Model**: `qwen2.5:7b-instruct` (Q4_K_M quantization, 7.6B parameters, 32k context) served
  by a local **Ollama** server, reachable at `http://localhost:11434/v1` — the same model this
  platform's own `.env` already configures for both `AI_PRIMARY_MODEL` and `AI_FALLBACK_MODEL`
  (Enterprise Readiness Plan A.6 first proved this exact model/server combination could answer
  one real Copilot query and one real RCA request; this is the first *repeatable, scored* run).
- **Harness**: [`backend/scripts/rca_benchmark.py`](../backend/scripts/rca_benchmark.py),
  mirroring `scale_bench.py`'s isolation pattern — its own temporary SQLite database and
  in-process `fakeredis`, so it never touches the real docker-compose Postgres/Redis and can be
  re-run repeatedly with no side effects. The LLM call itself, unlike the DB/queue, is 100%
  real: `RcaService.investigate()` runs completely unmodified, calling a real
  `OpenAICompatibleClient` over real HTTP.
- **Timeout**: `AI_REQUEST_TIMEOUT_SECONDS=240`, matching the real deployment's own `.env` — the
  first run of this script used `Settings`' 30s class default (tuned for a hosted/fast model)
  and two of five scenarios genuinely timed out generating a ~1500-token JSON response on a
  single local machine. That is itself a real, if unsurprising, finding: **this model is far
  too slow under this timeout budget for anything time-sensitive** (41–65 seconds per RCA call
  once given enough time to finish, see the table below) — a production deployment serving
  interactive users would need either a faster model, GPU acceleration, or an async/polling UX
  rather than a synchronous request-response RCA call.
- **Gold-incident set**: 5 fixed scenarios
  ([`app/rca_benchmark/gold_incidents.py`](../backend/app/rca_benchmark/gold_incidents.py)),
  each built via this platform's own real repositories/services (not literal dict mocks):
  `blocking_chain`, `sql_regression`, `capacity_exhaustion`, `multi_factor` (all four built with
  clear, unambiguous evidence for a specific cause), and `sparse_evidence` (deliberately built
  with none — the uncertainty-calibration bait).
- **Scoring**: 5 deterministic, evidence-groundable checks
  ([`app/rca_benchmark/scoring.py`](../backend/app/rca_benchmark/scoring.py)) — never a second
  LLM grading the first, per this codebase's own "deterministic, never an AI judgment" rule
  already stated in `app/reports/health_check.py`. See that module's docstring for exactly what
  each check does and does not catch.

Run it yourself: `cd backend && PYTHONPATH=. python scripts/rca_benchmark.py` (takes 4–5
minutes against a local CPU-bound 7B model; writes `docs/rca_benchmark_results.json`, the raw
data this report is drawn from). CI never runs this script — see its own module docstring for
why (no real LLM is reachable there); `backend/tests/test_rca_benchmark.py` covers the scoring
logic itself with `FakeLLMClient` on every CI run instead.

## Results

| Scenario | Passed all checks | Elapsed | probable_root_cause | confidence |
|---|---|---|---|---|
| `blocking_chain` | ❌ | 64.9s | `null` | `null` |
| `sql_regression` | ❌ | 48.5s | `null` | `null` |
| `capacity_exhaustion` | ❌ | 48.9s | `null` | `null` |
| `sparse_evidence` | ✅ | 38.4s | `null` | `null` |
| `multi_factor` | ❌ | 58.1s | `null` | `null` |

**1 of 5 scenarios passed every check** — and it's the one where the correct answer *was*
`null`/`null`. Per-check breakdown across all 5 runs:

| Check | Passed | Failed |
|---|---|---|
| `schema_adherence` | 5 | 0 |
| `confidence_consistency` | 5 | 0 |
| `no_fabricated_identifiers` | 5 | 0 |
| `uncertainty_calibration` (sparse_evidence only) | 1 | 0 |
| `engages_with_available_evidence` (the 4 clear-evidence scenarios) | 0 | 4 |

## Findings

**The model never hallucinated and never overclaimed confidence — genuinely good news.** Zero
schema failures, zero fabricated identifiers (SQL_IDs, tablespace names), zero cases of
claiming a confidence level without a cause or vice versa, and — on the one scenario built
with no real evidence — it correctly stayed at `confidence: null` rather than inventing a
specific story. This is exactly the safety property `app/ai/prompts.py`'s grounding rules ask
for, and it held up under a real model, not just `FakeLLMClient`.

**But it also never named a root cause on any of the four scenarios where the evidence clearly
supported one.** `engages_with_available_evidence` — added to this suite only after this run
first surfaced the gap (see that check's own docstring in `scoring.py`) — failed on
`blocking_chain`, `sql_regression`, `capacity_exhaustion`, and `multi_factor`, every scenario
*except* the one deliberately built to have nothing conclusive. Excess caution that makes RCA
output practically useless is its own distinct quality failure from hallucination, not a lesser
version of "being safe" — a benchmark that only measured overclaiming would have called this a
clean pass. The model consistently justified its refusal by pointing at *other, unrelated*
evidence categories being unavailable (e.g. citing missing `wait_profile`/`top_sql` on the
`blocking_chain` scenario, whose actual answer lived entirely in the session evidence it
already had), rather than reasoning from what it *did* have.

**One concrete case went further than excess caution into an inaccurate claim about the
evidence itself.** On `sql_regression`, the real evidence bundle handed to the model contained
`"sql_regressions": {"available": true, "open_regression_count": 1, "regressions": [{"sql_id":
"gldsqlregress1", "regression_kind": "PLAN_CHANGE", "ratio": 9.0, ...}]}` (verified directly by
calling `gather_rca_evidence` for this exact scenario) — yet the model's own
`data_completeness_note` stated *"no SQL regressions... data are available."* That is not
excess caution; it is a factually wrong claim about data that was directly in the prompt it was
told to reason from. This is worth flagging prominently rather than folding into the general
"declined to answer" finding above, even though no automated check here specifically catches
"describes the evidence bundle inaccurately in prose" (that would need semantic
understanding beyond what a deterministic rule can verify) — it was found by manually reading
this run's raw output, not by a check that will catch it again automatically next time.

**Real-model latency (41–65 seconds per RCA call) confirms the JSON-schema-adherence caveat
already on record was, this time, a non-issue.** `docs/PRODUCTION_CERTIFICATION.md`'s G7 row
previously noted "this real model does not reliably produce the exact requested JSON schema on
complex evidence, even with JSON mode enabled" (from A.6's single earlier trial) — across these
5 real calls, `schema_adherence` passed 5/5. That specific risk didn't reproduce this run; it
may still be real under different evidence shapes or prompt lengths this gold set didn't
happen to exercise, so it stays a documented caveat, not a resolved one.

## What this does and does not prove

- **Representative of this exact model/server/prompt combination, today.** A different model
  (even a different quantization of the same one), a different system prompt, or a future
  change to `app/ai/prompts.py`'s RCA rules could change every number above. Re-run the script
  after any such change to know whether it actually helped.
- **Not a claim about every possible incident shape.** 5 scenarios is enough to be falsifiable
  and honest, per this suite's own scope decision — not a statistically powered sample across
  the full space of real incidents.
- **Not evidence that the checks themselves are complete.** `no_fabricated_identifiers` is a
  heuristic (documented limits in `scoring.py`), and, as the `sql_regression` finding above
  shows directly, a model can misstate evidence in ways none of the five automated checks are
  built to catch — this benchmark measures what it measures, honestly, not everything that
  matters about RCA quality.
- **A likely next step this benchmark surfaces, not one it takes**: `app/ai/prompts.py`'s RCA
  rules currently emphasize "never overclaim on sparse evidence" without an equally explicit
  counter-instruction to *use* the evidence that is directly relevant even when other,
  unrelated categories are empty. Tightening that prompt and re-running this exact script would
  directly test whether it changes the `engages_with_available_evidence` result — left as a
  follow-up, not done here, since E.1's own scope is building and running the benchmark, not
  iterating on prompt engineering.

## Re-run after the evidence-governance rework (2026-09-11)

The follow-up work the section above named as a likely next step was actually done: a shared
deterministic evidence/investigation layer (`app/ai/investigation.py`) now pre-correlates raw
tool output into `Finding`s (blocker SID, real hold duration, waiter count, dominant wait class,
etc.) *before* the model ever sees it; `RcaService.investigate()`'s narrative was split into two
calls (Call 1: the exact proven 9-field core, kept minimal and unchanged in shape; Call 2: a
separate, smaller, seeded 6-field enterprise-narrative follow-up) after a single wide ~15-field
schema was found, via direct raw-API diagnosis, to make this model abandon the requested JSON
schema entirely; and `app/ai/prompts.py`'s RCA rules gained an explicit anti-refusal instruction
this section's predecessor called for. The gold set grew a 6th scenario
(`stale_evidence` — a real blocking chain built with 2-hour-old session telemetry, the
staleness-honesty bait) and the scorer grew two checks: `confidence_matches_coverage` (does the
model's self-reported confidence agree with a code-computed, model-independent evidence-coverage
label?) and `no_stale_evidence_presented_as_current` (self-gated — only applies when a finding
is actually computed as stale; fails if the narrative describes it with present-tense-immediacy
language). Same model, same Ollama server, same harness, same command
(`cd backend && PYTHONPATH=. python scripts/rca_benchmark.py`) — now 6 scenarios × 2 real LLM
calls each, ~75–150s per scenario, ~9 minutes total wall-clock this run.

### Results

| Scenario | Passed all checks | Elapsed | probable_root_cause | confidence |
|---|---|---|---|---|
| `blocking_chain` | ✅ | 146.6s | "Batch job (SID 100) holding a row lock..." | `medium` |
| `sql_regression` | ❌ | 84.1s | `null` | `null` |
| `capacity_exhaustion` | ❌ | 75.1s | `null` | `null` |
| `sparse_evidence` | ✅ | 56.1s | `null` | `null` |
| `multi_factor` | ❌ | 86.8s | `null` | `null` |
| `stale_evidence` (new) | ✅ | 101.5s | "Batch job (SID 400) holding a lock..." | `medium` |

**3 of 6 scenarios passed every check** (up from 1 of 5). Per-check breakdown:

| Check | Passed | Failed |
|---|---|---|
| `schema_adherence` | 6 | 0 |
| `confidence_consistency` | 6 | 0 |
| `no_fabricated_identifiers` | 6 | 0 |
| `confidence_matches_coverage` (new) | 4 | 2 |
| `engages_with_available_evidence` (5 non-sparse scenarios) | 2 | 3 |
| `uncertainty_calibration` (sparse_evidence only) | 1 | 0 |
| `no_stale_evidence_presented_as_current` (new, self-gated) | 1 | 0 |

### Honest findings — improved, and not improved

**Real, measured improvement: `engages_with_available_evidence` went from 0/4 to 2/5.** The
model now names a specific, correct root cause (with the right session IDs and roles) on
`blocking_chain` and on the new `stale_evidence` scenario — a scenario the original 5 never
tested, and one that adds a second dimension (does it also avoid describing 2-hour-old evidence
as current? — it did, cleanly). This is not nothing: the deterministic pre-correlation layer plus
the anti-refusal instruction moved the needle on exactly the failure mode the last run
identified, for the two scenarios where evidence is a two-entity blocking relationship.

**But `sql_regression`, `capacity_exhaustion`, and `multi_factor` still fail
`engages_with_available_evidence` — the core gap is not fixed.** The model still declines to
populate `probable_root_cause`/`confidence` on all three, for the same underlying reason as
before: it treats *unrelated* empty evidence categories (session/wait telemetry) as
disqualifying, even when the specific evidence for that scenario (an open SQL regression, a
97%-full tablespace, a real blocking pair alongside a real capacity alert) is present and clear.
`confidence_matches_coverage` — the new, model-independent anchor — fails on exactly the same two
of these three where `evidence_coverage` was computed as `"clear"` (`sql_regression`,
`capacity_exhaustion`; `multi_factor`'s coverage computed as `"partial"`, so declining confidence
there does not trip that check), which is a second, independent signal agreeing with the first:
this is still real excess caution, not a quirk of one check's wording.

**One genuine, measurable improvement in *how* it refuses: no factual misstatements this run,
where the baseline had one.** The original run's `sql_regression` case had the model claim *"no
SQL regressions... data are available"* while the evidence bundle handed to it contained exactly
that regression — a factually wrong claim about its own prompt, not just caution. This run's
`sql_regression` response instead correctly states *"A SQL regression with a plan change and
significant increase in execution time was detected"* in its `evidence_summary`, and its
`capacity_exhaustion` response correctly cites *"the database is currently at 97% capacity"* and
its `multi_factor` response correctly names *both* "one session is blocked by another" and "high
usage of the GLDDATA02 tablespace." In all three, **the model correctly identifies the relevant
fact in prose, then still declines to promote it into the structured `probable_root_cause`/
`confidence` fields.** That is a narrower, more specific failure than the baseline's — the
evidence is being read correctly now, just not committed to — but it is still a real failure of
the property this benchmark exists to measure, not a resolved one.

**Zero hallucination, zero schema failures, held across both runs — now over two calls per
scenario instead of one.** `schema_adherence` and `no_fabricated_identifiers` stayed 6/6 despite
each scenario now costing two real LLM calls instead of one; the two-call split's own purpose
(avoid the wide-schema drift a single ~15-field call was found to cause) did not trade schema
reliability for anything worse on the narrower, still-real 9-field core.

### What this re-run does and does not prove

- **The engagement gap is reduced, not closed.** Read the numbers as "the deterministic
  evidence layer plus an anti-refusal instruction help on some evidence shapes (a clean
  blocking relationship) and not yet on others (a regression, a capacity alert, two independent
  factors at once)" — not as "fixed." A model that still won't act on 3 of 5 clear-evidence
  scenarios is not yet a benchmark pass by this suite's own bar.
- **Confirms the two-call architectural split resolved the schema-drift problem it was built
  for**, at least for this gold set — 6/6 schema adherence across 12 real LLM calls (6
  scenarios × 2 calls) is a stronger signal than the original run's 5/5 across 5 calls, though
  still not a claim about every possible evidence shape.
- **A concrete, narrower next step this re-run surfaces**: the model is now reading the
  *right* evidence into prose but not committing it to the structured decision fields on 3
  scenarios — a targeted prompt change asking it to promote whatever specific fact it already
  identified in `evidence_summary` into `probable_root_cause` when that fact is unambiguous,
  rather than a broader instruction change, is the next falsifiable thing to try and re-measure
  against this exact script — left as a follow-up, not done here.
- Same scope caveats as the original run apply: 6 scenarios is still not a statistically
  powered sample, `no_fabricated_identifiers` is still a heuristic, and this remains
  representative of this exact model/server/prompt combination on this date, not a permanent
  property of the system.

Raw data for this run: `docs/rca_benchmark_results.json` (regenerated by the same script;
overwrites the file each run — this report is what preserves the historical comparison).
