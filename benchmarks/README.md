# Benchmarks

Contributor benchmarks for gated robotics. A benchmark here:

- builds on `ramen_robotics.gate` (or a module from `docs/rfcs/`) and reports every dispatch as a
  `GateDecision`, with outcome, receipt id, receipt class and latency;
- writes an audit file with `audit_path=` so its results can be checked again with
  `verify_audit_file`; published results use keyed runs, because trial receipts are not
  persistent audit proof;
- states its scenario source and license, and its live call count;
- reports latency as median, p95 and maximum over at least 50 live calls when it makes
  latency claims;
- runs offline in tests against `tests/fakes.py`.

Open a *Demo proposal* issue to discuss a new benchmark.
