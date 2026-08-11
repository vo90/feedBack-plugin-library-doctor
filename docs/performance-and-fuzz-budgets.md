# Performance and adversarial-input budgets

Date: 2026-08-11
Plugin version: `0.41.0`

## Enforced runtime budgets

| Boundary | Normal scan | Deep Audio | Enforcement |
|---|---:|---:|---|
| Package active-time deadline | 5 minutes | 15 minutes | Parent terminates and replaces the isolated worker; the package receives `package.validation-timeout`. Time paused for gameplay does not consume the deadline. |
| Worker RSS | 768 MiB | 1.5 GiB | Parent samples worker RSS. A multi-worker pool first degrades to one worker for attribution; a repeat records `package.validation-memory-limit`, terminates the worker, and continues. |
| Worker shutdown | 5 seconds plus bounded terminate/kill joins | Same | Non-cooperative processes cannot hold scanner shutdown indefinitely. |
| Parsed package structure | 2,000,000 values | Same | Validator stops with `package.validation-budget-exceeded`. |
| Declared archive content | 64 GiB | Same | Package reader rejects the package before unbounded extraction/read. |
| Reserved member reads | 4 GiB total | Same | Package reader stops further reads at the cumulative ceiling. |
| Package members | 50,000 | Same | Package enumeration is bounded. |

RSS telemetry is persisted with scan performance facts:
`peak_worker_rss_bytes`, `worker_rss_limit_bytes`,
`worker_memory_limit_exceeded`, and `worker_memory_restarts`.

## CI measurement profile

The normal matrix runs Python and JavaScript gates on current Windows and Linux
runners. The `constrained-scan` job additionally:

- sets `FEEDBACK_MAX_SCAN_WORKERS=1`;
- applies a 2 GiB process virtual-memory ceiling on Linux;
- runs the scanner/worker fault suite;
- prints and enforces the adversarial-corpus wall time and RSS growth.

The deterministic corpus budget is less than 5 seconds total and less than
128 MiB RSS growth. These are regression ceilings, not performance claims for a
full user library. The output keys are `adversarial_corpus_seconds` and
`adversarial_corpus_rss_growth_bytes`, making the measured values visible in CI.

## Versioned adversarial corpus

`tests/fixtures/adversarial_validator_corpus.json` is schema-versioned and
locks behavior around:

- valid, absolute, drive-qualified, traversal, doubled-separator, backslash,
  non-string, and empty package pointers;
- valid, truncated, commented, wrong-shape, non-finite, and mixed-hostile JSON;
- exact object copies and near-misses involving key order, booleans/integers,
  integers/floats, signed zero, and list order.

Every corpus case must return a bounded package report or a deterministic pure
predicate result. It must never escape the package root, crash the suite, or
silently turn a near-match into an automatic-repair identity.
