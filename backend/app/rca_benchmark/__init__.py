"""RCA quality benchmark suite (master plan Phase 20, Enterprise Readiness Plan E.1).

Scores app.services.rca_service.RcaService's real output against a fixed gold-incident set
using deterministic, evidence-groundable checks - never a second LLM judging the first, same
"deterministic, never an AI judgment" rule already stated in app/reports/health_check.py and
app/reports/builders.py's _recommended_investigation_items.

app/rca_benchmark/scoring.py holds the pure check functions; app/rca_benchmark/
gold_incidents.py holds the fixture builders. backend/scripts/rca_benchmark.py is the
standalone runner against a real LLM - mirrors backend/scripts/scale_bench.py's shape for
Phase 25, and is likewise never part of the pytest suite or CI (see that script's own
docstring for why: no real LLM is reachable in CI, and a benchmark that needs one stays a
manual, local check)."""
