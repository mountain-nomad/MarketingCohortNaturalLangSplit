---
name: experimentation
description: Experimentation rules for A/A and A/B design, sample size, MDE, power, significance, deterministic user assignment, and cohort export. Use whenever implementing experiment calculations or splitting cohorts.
---

# Experimentation

## Scope

For an authorized cohort, users should be able to:

- configure A/A or A/B experiments;
- set/derive baseline conversion;
- configure MDE;
- configure alpha/significance;
- configure statistical power;
- calculate required sample size;
- choose group allocation;
- create deterministic assignments;
- export control/treatment cohorts separately.

## Statistical engine

Do not reimplement established statistical formulas without an explicit, documented reason.

Primary library/repository:

```text
Python package: statsmodels
GitHub: https://github.com/statsmodels/statsmodels
```

Wrap statsmodels behind an internal application interface. Domain/UI code should not call statsmodels directly everywhere.

Conceptually:

```python
class ExperimentCalculator:
    def required_sample_size(...): ...
    def minimum_detectable_effect(...): ...
    def analyze_binary_metric(...): ...
    def confidence_interval(...): ...
```

Useful statsmodels areas include `statsmodels.stats.power` and `statsmodels.stats.proportion`. Verify the exact API against the pinned installed version before implementation.

Reference project for workflow/API ideas only:

```text
https://github.com/brightertiger/abverdict
```

Do not make it a core dependency by default. Check licenses before copying any external source code.

## Separation of responsibilities

```text
Statistics            -> statsmodels wrapper
Experiment config     -> our domain logic
User assignment       -> our deterministic assignment engine
Cohort extraction     -> our DWH layer
Exports               -> our export layer
RBAC/RLS              -> our access-control layer
```

## Deterministic assignment

Do not use non-reproducible per-run randomness such as `random.random()` for persisted experiments.

Assignment must be stable for the same experiment and user, e.g. conceptually:

```text
stable_hash(experiment_id + canonical_user_id) -> bucket
```

Use an explicitly defined stable hash algorithm; do not depend on language-runtime hash functions that may be salted or version-dependent.

## Sample-size behavior

If the selected cohort is smaller than required sample size, do not silently proceed as if the experiment is adequately powered. Surface the constraint and require an explicit product-defined action.

## A/A

A/A splits use the same deterministic assignment machinery. Where the product supports diagnostics, allow balance checks on approved pre-treatment covariates without leaking restricted data.

## Tests

Test:

- known statsmodels-backed outputs;
- invalid parameter ranges;
- deterministic repeat assignment;
- no overlap between groups;
- allocation proportions within expected tolerance for large synthetic populations;
- stability after process restart;
- insufficient cohort size;
- permission enforcement around experiment creation/export.
