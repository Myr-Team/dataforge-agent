# P2-C Task 1: Capability Pack Registry

## Scope

- Added six generic, data-only capability packs in `backend/data/capability_packs.json`.
- Added `CapabilityPack` and `CapabilitySelection` contracts in `backend/schemas.py`.
- Added deterministic, name-independent selection in `backend/capability_packs.py`.
- Added selection, anti-hardcoding, weak-evidence, relationship, rename-invariance, and Chinese-goal coverage in `tests/test_capability_packs.py`.

## Selection contract

- The selector reads only the provided business goal, semantic schema roles, metric families, entity relationships, time coverage, and quality summary.
- It never reads workspace names, dataset names, file names, or arbitrary profile text.
- It returns no more than three opportunity packs. Missing opportunity evidence returns only `risk_data_readiness` with explicit evidence gaps.
- Pack data contains questions, evidence requirements, validation methods, and artifact sections only. It does not contain scores, conclusions, winners, named opportunities, or preferred industries.

## Test-first evidence

1. The initial `tests/test_capability_packs.py` run failed at collection because `backend.capability_packs` did not exist.
2. The relationship-specific regression failed while relationship values were treated as opaque values, then passed after semantic token normalization was added.
3. The Chinese-goal regression failed before Chinese token normalization and generic Chinese goal concepts were added, then passed.
4. Review follow-up tests reproduced three fail-open paths: invalid quality could be treated as complete, malformed temporal coverage could add a suitability contribution, and nested `name`/`type`/`family` values could act as semantic fields.

## Review follow-up

- Quality now accepts only a mapping with finite numeric completeness-or-missing and duplicate signals. Missing, conflicting, non-numeric, boolean, non-finite, or out-of-range values are invalid and force `risk_data_readiness`.
- Temporal coverage now requires `available: true` and either a positive numeric `periods`/`count` or an explicit, ordered evidence range with ISO-parseable or finite numeric `start` and `end` values. Raw names, strings that are not time values, booleans, false flags, zero, and malformed structures cannot contribute.
- Schema input is filtered to the explicit `schema_roles`, `metric_families`, `entity_relationships`, and `temporal_coverage` contract. Semantic values must be direct strings from the data-driven pack vocabulary; nested objects and raw column/dataset fields are ignored.

## Review R2 follow-up

- Time ranges reject mixed aware/naive ISO datetimes, invalid endpoints, incomparable values, and reversed ranges without raising; any invalid range makes temporal evidence unavailable.
- `periods` and `count` are treated as aliases. Each must be the same finite positive integer when both are supplied; conflicts and fractional or malformed values make temporal evidence unavailable.

## Verification

- `python -m pytest tests/test_capability_packs.py -q` -> 26 passed after R2 follow-up.
- `python -m pytest tests/test_agent_generalization_contract.py tests/test_evidence_bundle.py -q` -> 6 passed.
- `python -m compileall -q backend tests` -> passed.
- `python -m json.tool backend/data/capability_packs.json` -> passed.
- `git diff --check` -> passed.
- `python -m pytest -q` after the final Chinese-goal normalization change -> 713 passed, 1 known MAF experimental warning.

## Remaining integration work

Task 1 exposes the registry and deterministic selector only. Task 2 must pass selected pack questions, validation methods, and artifact sections into routing, MAF context, run trace, and artifacts while keeping the evidence rubric authoritative.
