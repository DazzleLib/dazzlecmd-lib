# Roadmap — dazzlecmd-lib

The living roadmap is tracked in **[Issue #1](https://github.com/DazzleLib/dazzlecmd-lib/issues)** (evergreen). This file is a summary.

## Status

Extracted from the dazzlecmd monorepo into this standalone DazzleLib repository at **v0.8.55** (2026-06-24). Pre-1.0; API may change during MINOR bumps until 1.0.

## Phases

| Phase | Focus | Status |
|-------|-------|--------|
| Extraction | Standalone repo, PyPI publish, consumer cutover (dazzlecmd) | In progress |
| Stabilize API | Settle the `AggregatorEngine` / `FQCNIndex` / `RunnerRegistry` / mode surfaces toward 1.0 | Planned |
| Docs | `docs/` guide for building a `dz`-pattern CLI; platform-support matrix | Planned |
| 1.0 | API freeze + semantic-versioning guarantees | Planned |

## Consumers

[dazzlecmd](https://github.com/DazzleTools/dazzlecmd), `amdead`, `wtf-windows` — all pin `dazzlecmd-lib>=0.8.0,<1.0`.

See the [CHANGELOG](CHANGELOG.md) for released changes.
