"""Default meta-commands for dazzlecmd-pattern aggregators.

Exposes the built-in ``list``, ``info``, ``kit``, ``version``, ``tree``,
and ``setup`` commands as parser factories + handlers + render functions.
``AggregatorEngine`` auto-registers them on construction via
``register_all()``; aggregators can opt out (``include_default_meta_commands=False``),
unregister specific ones (``engine.meta_registry.unregister("tree")``),
or override them (``engine.meta_registry.override("info", handler=...)``).

Public surface for aggregator authors:

- ``render_*(args, projects, ...) -> int``: the printing logic for each
  command, decoupled from engine context. Import these to **compose** —
  call ``render_info()`` from your override, then append domain fields.

- ``*_parser_factory(subparsers)``: argparse subparser setup. Import
  these to reuse the argument shape while replacing the handler.

- ``*_handler(args, engine, projects, kits, project_root) -> int``: the
  handlers the registry calls. These are thin wrappers around ``render_*``
  that unpack engine context. Override at the handler level when your
  domain logic needs ``engine`` or ``project_root``.

- ``register_all(registry)``: bulk-register every default. Invoked by the
  engine at construction time.

- ``register_selected(registry, include=[...])``: opt-in helper — register
  only the defaults you want.

These implementations are intentionally **minimal**. They cover the
common-case output for a generic aggregator. Aggregators with rich
domain fields (diagnostic badges, Docker-specific rendering, collision
markers, terminal-width wrapping, etc.) should override the handler and
compose with the stock render function OR replace it outright.
"""

from __future__ import annotations

import json as _json
import sys as _sys
from typing import Iterable, Optional

from . import colors as _colors
from .core import is_constitutional as _is_constitutional


# SD-E (slice 1): the shared display utils + layout constants moved VERBATIM to
# ``rendering.py``; re-exported here so existing importers keep resolving
# unchanged (parsers.py: MIN_DESC_WIDTH/TERM_SIZE_FALLBACK; the render_* below).
from .rendering import (  # noqa: F401  (re-exported for the public surface)
    KIT_NAME_COL,
    MIN_DESC_WIDTH,
    SUMMARY_INDENT,
    TERM_SIZE_FALLBACK,
    _constitutional_entry,
    _print_legend_entry,
    _term_width,
    _wrap_description,
    build_list_entries,
    render_info,
    render_kit_list,
    render_list,
    render_setup_listing,
    render_tree,
    render_version,
)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def list_parser_factory(subparsers):
    """Register the ``list`` subparser.

    Flags:
        --namespace / -n: filter by namespace
        --kit / -k: filter by kit (canonical OR virtual)
        --tag / -t: filter by taxonomy.tags
        --platform / -p: filter by platform
        --show: content selector (default/canonical/alias/all)
    """
    p = subparsers.add_parser("list", help="List available tools")
    p.add_argument("--namespace", "-n", help="Filter by namespace")
    p.add_argument("--kit", "-k", help="Filter by kit (canonical OR virtual)")
    p.add_argument("--tag", "-t", help="Filter by tag")
    p.add_argument("--platform", "-p", help="Filter by platform")
    p.add_argument(
        "--show",
        choices=["default", "canonical", "alias", "all"],
        default=None,
        help=(
            "Content selector. 'default' (alias-preferred): virtual-kit "
            "aliases replace their canonical targets. 'canonical': "
            "canonicals only (script-stable legacy view). 'alias': aliases "
            "only. 'all': both canonicals and aliases. Falls back to "
            "config key 'list_view' then to 'default' if unset."
        ),
    )
    p.add_argument(
        "--show-hidden", action="store_true",
        help="Include tools hidden via the 'hidden_tools' config (still dispatchable).",
    )
    p.set_defaults(_meta="list")


def list_handler(args, engine, projects, kits, project_root) -> int:
    """Default handler for ``list``. Passes engine to render_list so
    aggregators with virtual kits / FQCN indexes get the full sectioned
    output. Aggregators that don't have an engine context can call
    ``render_list(args, projects)`` directly for plain flat output."""
    return render_list(args, projects, engine=engine)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def info_parser_factory(subparsers):
    """Register the ``info`` subparser.

    Flags:
        tool: tool name or FQCN to inspect
        --raw: show the manifest as declared, without conditional-dispatch
            resolution.
        --platform SPEC: preview runtime resolution for a specific
            platform (e.g. ``linux``, ``linux.ubuntu``, ``windows``).
    """
    p = subparsers.add_parser("info", help="Show detailed info about a tool")
    p.add_argument("tool", help="Tool name or FQCN to inspect")
    p.add_argument(
        "--raw",
        action="store_true",
        help="Show the manifest as declared, without conditional-dispatch resolution.",
    )
    p.add_argument(
        "--platform",
        metavar="SPEC",
        help=(
            "Preview runtime resolution for a specific platform "
            "(e.g. 'linux', 'linux.ubuntu', 'windows'). Does not check "
            "PATH; uses declared platform block."
        ),
    )
    p.set_defaults(_meta="info")


# ---------------------------------------------------------------------------
# Runtime display helpers for `info`
#
# Ported verbatim from dazzlecmd cli.py:889-1116 in v0.7.32 (4b-T9 info-parity
# port). Provides the runtime-resolution display that consumers (amdead,
# wtf-windows, sysdiagnose, future personal aggregators) need when their
# users run ``aggregator info <tool>`` against a tool with conditional
# runtime dispatch (per-platform blocks, prefer ladders, ``{{var}}``
# template references, Docker fields, etc.).
# ---------------------------------------------------------------------------


def info_handler(args, engine, projects, kits, project_root) -> int:
    """Default handler for ``info``. Delegates to ``render_info`` with
    engine context so alias FQCN lookups resolve transparently."""
    return render_info(args, projects, engine=engine)


# ---------------------------------------------------------------------------
# kit (list + status)
# ---------------------------------------------------------------------------


def kit_parser_factory(subparsers):
    """Register the ``kit`` subparser and its nested ``list``/``status``."""
    p = subparsers.add_parser("kit", help="Manage {kits, aggregators, virtual kits, ...}")
    sub = p.add_subparsers(dest="kit_command")

    kit_list_p = sub.add_parser(
        "list", help="List available kits, or tools in a kit"
    )
    kit_list_p.add_argument(
        "name", nargs="?", default=None, help="Kit name to show tools for"
    )
    kit_list_p.set_defaults(_meta="kit_list")

    # Bare `kit` with no sub is treated as `kit list`
    p.set_defaults(_meta="kit_list")


def kit_list_handler(args, engine, projects, kits, project_root) -> int:
    # Pass the engine through (the renderer-contract convention): unlocks
    # config-aware status, #48 drill-in columns, and the virtual-kit alias
    # drill-in for every consumer.
    return render_kit_list(args, kits, projects, engine=engine)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def version_parser_factory(subparsers):
    p = subparsers.add_parser("version", help="Show version info")
    p.set_defaults(_meta="version")


def version_handler(args, engine, projects, kits, project_root) -> int:
    return render_version(engine)


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


def tree_parser_factory(subparsers):
    p = subparsers.add_parser(
        "tree",
        help="Visualize the aggregator tree (kits and tools)",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument(
        "--depth", type=int, default=None,
        help="Limit display depth (1=kits only, 2+=include tools)",
    )
    p.add_argument(
        "--kit", "-k", default=None,
        help="Show only this kit's subtree",
    )
    p.add_argument(
        "--show-disabled", action="store_true",
        help="Include disabled kits in the output",
    )
    p.add_argument(
        "--show-hidden", action="store_true",
        help="Include tools hidden via the 'hidden_tools' config (still dispatchable).",
    )
    p.add_argument(
        "--show-empty", action="store_true",
        help="Include enabled kits that have no tools (shown as childless branches).",
    )
    p.set_defaults(_meta="tree")


def tree_handler(args, engine, projects, kits, project_root) -> int:
    return render_tree(args, engine, projects, kits, project_root)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def setup_parser_factory(subparsers):
    p = subparsers.add_parser(
        "setup",
        help="Run a tool's declared setup script (install deps, build, etc.)",
    )
    p.add_argument(
        "tool", nargs="?", default=None,
        help="Tool name or FQCN. Omit to list tools with setup declared. "
             "Name the aggregator itself to run its own PATH bootstrap.",
    )
    p.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompts (any setup target that asks; "
             "the aggregator self-setup prompts before changing PATH).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Show what setup would do without doing it: the resolved "
             "command for a tool, the would-be PATH change for the "
             "aggregator itself.",
    )
    p.add_argument(
        "--emit-shell-fix", action="store_true",
        help="Machine channel: print exactly the one line that heals the "
             "CURRENT shell's PATH (writes per-shell fix scripts to the "
             "temp dir). Pipe-friendly; diagnostics go to stderr.",
    )
    p.set_defaults(_meta="setup")


def _self_setup_identity(engine):
    """The aggregator's own names + best-effort package identity.

    Returns (command_names, package_name, package_location). The
    ``python -m`` package (when the process was launched that way) is
    accepted as a self-name too, so whatever the user typed after
    ``python -m`` also works as the setup target.
    """
    from . import self_setup as _self_setup

    command_names = []
    for candidate in (getattr(engine, "command", None),
                      getattr(engine, "name", None)):
        if candidate and candidate not in command_names:
            command_names.append(candidate)

    package_name = _self_setup.python_dash_m_target()
    package_location = None
    if package_name:
        mod = _sys.modules.get(package_name)
        mod_file = getattr(mod, "__file__", None)
        if mod_file:
            import os as _os
            package_location = _os.path.dirname(mod_file)
        if package_name not in command_names:
            command_names.append(package_name)
    return command_names, package_name, package_location


def setup_handler(args, engine, projects, kits, project_root) -> int:
    """Default handler for ``setup``.

    With no tool argument: lists tools that declare a setup block.
    With the aggregator's own name (root token, alias, or the
    ``python -m`` package): runs the self-setup PATH bootstrap
    (dazzlecmd#103) instead of tool resolution.
    With a tool argument: resolves the tool's setup block (platform +
    user overrides + _vars) and executes the resolved command.
    """
    from . import self_setup as _self_setup

    tool_name = getattr(args, "tool", None)

    command_names, package_name, package_location = \
        _self_setup_identity(engine)

    if not tool_name:
        orphan_tail = list(getattr(args, "level_args", []) or [])
        if orphan_tail:
            # Tester finding (2026-07-19): a `--` tail with no target
            # was silently dropped; say so instead.
            print(_colors.warn(
                "note: no setup target given; args after '--' were "
                f"ignored: {orphan_tail}. Usage: setup <target> -- "
                "<target-args>"), file=_sys.stderr)
        hint = _self_setup.first_run_hint(
            command_names, package_name=package_name,
            package_location=package_location)
        if hint:
            print(_colors.warn(hint), file=_sys.stderr)
        return render_setup_listing(projects)

    level_args = list(getattr(args, "level_args", []) or [])

    if tool_name in command_names:
        if level_args:
            # Variant-2 contract (#104): the self-target defines no
            # level-params yet; the space after `--` is reserved.
            print(_colors.warn(
                "note: self-setup takes its options before '--' "
                f"(--yes/--dry-run); ignoring reserved trailing args: "
                f"{level_args}"), file=_sys.stderr)
        # Shadow visibility (#103 criterion 5): if a real tool bears the
        # aggregator's name, say which one and how to reach its setup.
        shadowed, _sctx = engine.find_project(tool_name)
        if shadowed is not None:
            shadow_name = shadowed.fqcn or shadowed.name
            print(_colors.warn(
                f"note: {tool_name!r} is also a tool ({shadow_name}) -- "
                f"running the aggregator's self-setup; use 'setup "
                f"{shadow_name}' for the tool."), file=_sys.stderr)
        return _self_setup.run_self_setup(
            command_names,
            package_name=package_name,
            package_location=package_location,
            assume_yes=getattr(args, "yes", False),
            dry_run=getattr(args, "dry_run", False),
            emit_shell_fix=getattr(args, "emit_shell_fix", False),
        )

    # Resolve the tool via engine.find_project — supports short name,
    # canonical FQCN, alias FQCN, and kit-qualified shortcuts uniformly.
    # engine is mandatory in the registry dispatch path; library
    # consumers that build their own dispatcher must pass an engine.
    # Advisories (tool-not-found, no-setup) use warn() -> YELLOW.
    # Genuine error paths (override-file parse/read failures) use error() -> BRIGHT_RED.
    project, ctx = engine.find_project(tool_name)
    if project is None:
        print(_colors.warn(f"Tool {tool_name!r} not found."), file=_sys.stderr)
        return 1
    matches = [project]

    if len(matches) > 1:
        print(_colors.warn(f"Multiple tools named {tool_name!r}:"), file=_sys.stderr)
        for p in matches:
            print(f"  {p.fqcn or p.name}", file=_sys.stderr)
        return 1

    project = matches[0]
    setup = project.setup
    if not setup:
        print(
            _colors.warn(
                f"Tool {project.fqcn or project.name!r} has no setup declared."
            ),
            file=_sys.stderr,
        )
        return 1

    # Resolve the setup block via the library's resolver (handles
    # platform selection, user overrides, _vars substitution).
    try:
        from dazzlecmd_lib.setup_resolve import resolve_setup_block

        resolved = resolve_setup_block(project)
    except _json.JSONDecodeError as exc:
        print(
            _colors.error(f"Error: user override file is not valid JSON: {exc}"),
            file=_sys.stderr,
        )
        return 1
    except OSError as exc:
        print(
            _colors.error(f"Error: cannot read user override file: {exc}"),
            file=_sys.stderr,
        )
        return 1
    except Exception as exc:
        print(_colors.error(f"Error resolving setup: {exc}"), file=_sys.stderr)
        return 1

    if resolved is None:
        print(
            _colors.warn(
                f"Tool {project.fqcn or project.name!r} has no executable setup."
            ),
            file=_sys.stderr,
        )
        return 1

    command = resolved.get("command")
    if not command:
        print(
            _colors.warn(
                f"Tool {project.fqcn or project.name!r} has no setup command "
                f"for this platform."
            ),
            file=_sys.stderr,
        )
        return 1

    # Execute the resolved command. The engine is a dumb dispatcher —
    # we run the author-declared command via the platform shell.
    import subprocess as _subprocess

    # Variant-2 contract (#104): forward the level-args (everything the
    # user wrote after `--`) into the tool's setup invocation -- the
    # v0.7.46 documented forwarding, wired for the first time.
    if level_args:
        from .verb_contracts import join_for_shell
        command = f"{command} {join_for_shell(level_args)}"

    dry_run = getattr(args, "dry_run", False)
    label = "[dry-run] Would run" if dry_run else "Running"
    print(f"{label} setup for {project.fqcn or project.name}...")
    print(f"  {command}")
    _sys.stdout.flush()  # flush before subprocess to avoid output interleaving

    # Verb-level --dry-run: show the resolved invocation, run nothing.
    if dry_run:
        return 0

    result = _subprocess.run(command, shell=True, cwd=project.directory)
    return result.returncode


# ---------------------------------------------------------------------------
# Bulk registration
# ---------------------------------------------------------------------------


# Canonical mapping: meta-command name -> (parser_factory, handler)
_DEFAULTS = {
    "list": (list_parser_factory, list_handler),
    "info": (info_parser_factory, info_handler),
    "kit": (kit_parser_factory, kit_list_handler),  # parser sets _meta=kit_list by default
    "version": (version_parser_factory, version_handler),
    "tree": (tree_parser_factory, tree_handler),
    "setup": (setup_parser_factory, setup_handler),
}

# Sub-meta handlers (kit has the kit_list sub-command).
# Separately registered so the engine's dispatch can route
# kit_list -> kit_list_handler.
_SUB_HANDLERS = {
    "kit_list": kit_list_handler,
}


def register_all(registry) -> None:
    """Register every default meta-command against the given registry.

    Called by ``AggregatorEngine.__init__`` when
    ``include_default_meta_commands=True`` (the default).

    This registers the top-level commands (list, info, kit, version,
    tree, setup). Nested meta tags (kit_list) are registered
    via ``_register_sub_handlers`` so the registry's dispatch can route
    them.
    """
    for name, (parser_factory, handler) in _DEFAULTS.items():
        registry.register(name, parser_factory, handler)
    _register_sub_handlers(registry)


def register_selected(
    registry, include: Optional[Iterable[str]] = None
) -> None:
    """Register only the named defaults.

    Useful when an aggregator wants an explicit subset. Unknown names
    raise ``KeyError``.

    Example::

        register_selected(registry, include=["list", "info", "version"])
        # tree, setup, kit excluded
    """
    if include is None:
        register_all(registry)
        return

    for name in include:
        if name not in _DEFAULTS:
            raise KeyError(
                f"Unknown default meta-command: {name!r}. "
                f"Available: {sorted(_DEFAULTS.keys())}"
            )
        parser_factory, handler = _DEFAULTS[name]
        registry.register(name, parser_factory, handler)

    # If kit is included, also register the sub handlers
    if "kit" in include:
        _register_sub_handlers(registry)


def _register_sub_handlers(registry) -> None:
    """Register the sub-meta handlers (kit_list).

    These don't have parser factories (the kit parser factory builds
    the nested subparsers); they only need dispatch-side routing entries
    so ``args._meta = "kit_list"`` resolves to the right handler.
    """
    # A minimal "parser factory" that does nothing — the kit parser
    # already built the subparser when kit was registered.
    def _noop_parser(subparsers):
        pass

    for name, handler in _SUB_HANDLERS.items():
        if name not in registry:
            registry.register(name, _noop_parser, handler)
