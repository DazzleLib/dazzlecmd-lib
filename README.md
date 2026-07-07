# dazzlecmd-lib

[![PyPI](https://img.shields.io/pypi/v/dazzlecmd-lib?color=green)](https://pypi.org/project/dazzlecmd-lib/)
[![Release Date](https://img.shields.io/github/release-date/DazzleLib/dazzlecmd-lib?color=green)](https://github.com/DazzleLib/dazzlecmd-lib/releases)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20BSD-lightgrey.svg)](docs/platform-support.md)

The aggregator/dispatch engine behind the **dazzlecmd** pattern: tool discovery, kit/FQCN dispatch, the runtime registry, and the `DazzleEntity` continuum object model. It is a standalone framework — it does **not** require dazzlecmd to be installed; dazzlecmd is just its first consumer.

## Build your own `dz`-pattern CLI in ~10 lines

```python
from dazzlecmd_lib import AggregatorEngine

def main():
    engine = AggregatorEngine(
        name="my-tools",
        command="mt",
        tools_dir="tools",
        manifest=".mt.json",
        version_info=("1.0", "1.0.0_main_1"),
    )
    return engine.run()
```

That gives you `mt list`, `mt info <tool>`, `mt kit`, `mt version`, `mt tree`, `mt setup` — plus the whole FQCN surface: a property store (`mt .note=hi`, `mt prop list`), bang-path addressing (`mt :.meta`, `mt :core:tool.version`), and a derived tree where every axis, verb, and rung is inspectable (`mt info <anything>`) — for free. Customize via the meta-command registry:

```python
engine.meta_registry.register("mycmd", parser_factory, handler)
engine.meta_registry.override("list", handler=my_custom_list)
engine.meta_registry.unregister("tree")
```

## What's inside

- **`AggregatorEngine`** — a configurable CLI tool aggregator (discovery + dispatch).
- **`FQCNIndex`** — dual-index lookup for Fully-Qualified Collection Names (`kit:tool`, `aggregator:kit:tool`).
- **`RunnerRegistry`** — extensible runtime dispatch (Python, PowerShell, passthrough, …).
- **`MetaCommandRegistry`** — per-engine meta-command registry (override/extend the stock commands).
- **`default_meta_commands`** — stock `list` / `info` / `kit` / `version` / `tree` / `setup`.
- **`ConfigManager`** — per-aggregator config reading/writing.
- The **mode subsystem** — embedded / submodule / symlink tool-source detection and the dev↔publish toggle.
- The **`DazzleEntity`** object model and the kit/continuum machinery (built on the `dazzle-lib` bedrock).
- **`fqcn_grammar`** — the bang-path address grammar: `:` selects a child, `.` reads a property, `:.` steps into a fiber (hidden machinery), `:+` steps to the parent. Forgiving canonicalization with visible rewrites (`-> dz.note (canonical)`), never silent.
- **`property_store` / `prop_commands`** — the property surface (`mt .note=hi`, `mt prop get/set/add/delete/list`) over a discoverable `properties.json`: validated keys, registered defaults (`tool (default)`), one-node value aliases, and **derived read-only properties** (`engine.derived_reads` — values answered from an item's own data, shadow-proof).
- **`fqcn_tree`** — the **derived tree**: every axis, rung, verb pole, and (via engine extensions) instance becomes an addressable node, derived from the live structures rather than hand-listed. Channels-by-existing, the verbosity **cascade** (one write on an outer node quiets its subtree), prefix-aware aliases with lazy key migration, and the seams an aggregator customizes: `engine.tree_extensions`, `engine.tree_mounts`, `engine.tree_aliases`.
- **`verb_axis`** — verbs as poles on signed-rank axes (`enable↔disable`, `attach↔detach`, …) with universal `on`/`off` synonyms, the containment ladder (`fiber < lib < internaltool < tool < kit < aggregator < supra`), and the composed verb×level space that routes `mt <verb> <target>` with zero per-verb branches.
- **`interrogation`** — the identity/state card machinery behind `mt info` at every level.

## The DazzleLib stack

`dazzlecmd-lib` is an upper-layer member of the [DazzleLib](https://github.com/DazzleLib) library stack. It builds on the bedrock and file-ops layers:

- [`dazzle-lib`](https://github.com/DazzleLib/dazzle-lib) — Protocols, TypedDict schemas, the continuum/state primitives.
- [`dazzle-filekit`](https://github.com/DazzleLib/dazzle-filekit) — cross-platform file operations + metadata preservation.
- [`unctools`](https://github.com/DazzleLib/UNCtools) — UNC / drive-type path tools (Windows volume routing).

Consumers built on `dazzlecmd-lib` include [dazzlecmd](https://github.com/DazzleTools/dazzlecmd), `amdead`, and `wtf-windows`.

## Install

```bash
pip install dazzlecmd-lib
```

### Developing alongside a consumer (editable, shadow-proof)

When a consumer (e.g. dazzlecmd) is installed from PyPI, its `dazzlecmd-lib` dependency comes from PyPI too. To develop against a local checkout, install it editable **with `--no-deps`** so the local copy wins over any PyPI copy:

```bash
pip install -e C:/code/dazzlecmd-lib --no-deps
```

## Versioning

Pre-1.0: the library reserves the right to make breaking changes during MINOR bumps until 1.0. Consumers should pin `dazzlecmd-lib>=0.X.Y,<1.0`.

History through 0.8.55 lives in the [dazzlecmd](https://github.com/DazzleTools/dazzlecmd) monorepo git log (under `packages/dazzlecmd-lib/`), from which this repository was extracted on 2026-06-24. See [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and roadmap: [Issue #1 (Roadmap)](https://github.com/DazzleLib/dazzlecmd-lib/issues).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
