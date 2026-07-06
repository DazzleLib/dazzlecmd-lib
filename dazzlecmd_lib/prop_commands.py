"""The ``prop`` verb family -- CRUD over the per-FQCN property store.

One implementation serves BOTH surfaces (v2 contract R1.8): the explicit
verbs (``dz meta prop {get,set,add,delete}`` / shortname ``dz prop ...``)
and the CLI sugar (``dz .note "hi"`` -- which routes to :func:`cmd_upsert`
via the intercept). Write strictness (R1.1/F6): ``add`` = must-not-exist,
``set`` = must-exist, the sugar UPSERTS but ECHOES which it did ("added X
(new)" vs "updated X") so a typo'd path self-diagnoses. ``delete`` is
explicit-only -- no sugar spelling deletes.

Paths are canonicalized (the FIBER_ROOTS forgiveness rule) before any
store access; when forgiven, the canonical form is echoed once (AC-11,
whole path, case untouched). Validated keys (e.g. ``<root>.level``)
consult :data:`VALIDATED_KEYS` on the WRITE path, so verb and sugar
validate identically by construction (C-7).

All functions print their outcome and return an exit code. ``0`` =
success; ``1`` = not-found / validation / strictness errors (parse errors
raise ``FQCNParseError`` for the caller's uniform rendering).
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from dazzlecmd_lib.fqcn_grammar import (
    FQCNParseError,
    PLANE_SUBKEY,
    canonicalize,
    parse,
    segment_planes,
    unparse,
)
from dazzlecmd_lib.property_values import parse_property_value


# {canonical_key: validator} -- a validator raises ValueError with a
# user-renderable message to REJECT a value. Registered by features that
# constrain a property (3e" registers ``<root>.level`` against the
# LEVEL_CONTINUUM rungs at call time). Consulted on EVERY write path, so
# the sugar and the explicit verbs validate identically (C-7).
VALIDATED_KEYS: Dict[str, Callable[[Any], None]] = {}


def register_validated_key(key: str, validator: Callable[[Any], None]) -> None:
    """Register (or replace) the validator for a canonical property key."""
    VALIDATED_KEYS[key] = validator


# The one-node value aliases: a fiber-plane AXIS NODE whose bare VALUE is
# backed by a property (the axis's value IS the property -- e.g.
# `<root>:.level` reads/writes `<root>.level`, so `dz :.level=bogus` hits
# the level VALIDATOR instead of silently storing an inert shadow key --
# the 2026-07-04 sweep's F2 finding). EXACT-key only: `:.level:kit` (the
# rung) and `:.level.foo` (a property ON the axis node) are untouched.
NODE_VALUE_ALIASES: Dict[str, str] = {}


def register_node_value_alias(alias_key: str, target_key: str) -> None:
    """Register a bare-node value alias (see NODE_VALUE_ALIASES)."""
    NODE_VALUE_ALIASES[alias_key] = target_key


# System DEFAULTS for keys whose unset state has a meaning (the layered
# read's degenerate form -- tester REAL-BUG 2: `dz level` and `dz :.level`
# must agree AFTER a delete; both now resolve unset -> the registered
# default, echoed as such). The full derived->system->store layering is
# the #77 mechanism; this registry is its system-default layer arriving
# with its first customer.
KEY_DEFAULTS: Dict[str, Any] = {}


def register_key_default(key: str, default: Any) -> None:
    """Register the system default read when ``key`` is unset."""
    KEY_DEFAULTS[key] = default


def _canonical(engine, path_text: str, *,
               apply_value_alias: bool = True) -> str:
    """Canonicalize ``path_text`` against the RUNNING aggregator's root
    (SELF-rooted -- ``engine.command``, never a hardcoded ``dz``; R1.2),
    echoing the canonical form once when the spelling was forgiven,
    then applying the one-node VALUE aliases (exact-key). LISTING passes
    ``apply_value_alias=False`` (tester REAL-BUG 1): a family listing is
    about the NODE's children, not the node's VALUE -- `dz :.level:.`
    must list the fiber family under `dz:.level`, never `dz.level`."""
    text, forgiven = canonicalize(path_text, implicit_root=engine.command)
    if forgiven:
        print(f"-> {text} (canonical)")
    if apply_value_alias:
        target = NODE_VALUE_ALIASES.get(text)
        if target is not None:
            print(f"-> {target} (the node's value)")
            return target
    return text

def _warn_casefold_collision(engine, key: str) -> None:
    """R1.5's Windows env footgun: warn when a sub-key write casefold-
    collides with an existing sibling (``.env-vars:DEBUG`` vs ``:debug``
    collapse to ONE arbitrary winner in a Windows environment export)."""
    parsed = parse(key)
    planes = segment_planes(parsed)
    if PLANE_SUBKEY not in planes:
        return
    # the parent family = the path up to (excluding) the LAST segment
    parent = parsed.root + unparse(
        type(parsed)("", parsed.segments[:-1])
    )
    last = parsed.segments[-1].name
    for sibling in engine.property_store.list_prefix(parent):
        if sibling == key:
            continue
        tail = sibling[len(parent):]
        if tail[1:].casefold() == last.casefold() and tail[1:] != last:
            print(
                f"Warning: '{sibling}' differs from '{key}' only by case "
                f"-- on Windows, environment exports collapse these to "
                f"one arbitrary winner.",
                file=sys.stderr,
            )


def _write(engine, key: str, value: Any) -> Optional[int]:
    """Shared validated write. Returns an exit code on rejection."""
    validator = VALIDATED_KEYS.get(key)
    if validator is not None:
        try:
            validator(value)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2  # exit-2 parity with argparse choice errors (R1.7)
    _warn_casefold_collision(engine, key)
    if isinstance(value, str) and value.lstrip()[:1] in ("[", "{"):
        # Looks like JSON that didn't parse -- almost always the shell
        # stripped the inner quotes (cmd.exe's C-runtime rules eat
        # unescaped double quotes: [\"a\"] arrives as [a]). Stored as a
        # plain string; say so instead of degrading silently.
        print(
            "note: stored as a plain STRING (not JSON) -- if you meant "
            "JSON, escape the inner quotes for your shell "
            '(cmd.exe: dz .x=[\\"a\\",\\"b\\"]).',
            file=sys.stderr,
        )
    engine.property_store.set(key, value)
    return None


def cmd_get(engine, path_text: str) -> int:
    """``prop get <path>`` -- print the stored value (or the registered
    system default, marked as such)."""
    key = _canonical(engine, path_text)
    value = engine.property_store.get(key)
    if value is None:
        if key in KEY_DEFAULTS:
            print(f"{KEY_DEFAULTS[key]} (default)")
            return 0
        print(f"{key} is not set")
        return 1
    print(value)
    return 0


def cmd_set(
    engine, path_text: str, value_token: str,
    named_ranks: Optional[Mapping[str, int]] = None,
) -> int:
    """``prop set <path> <value>`` -- STRICT: the key must already exist."""
    key = _canonical(engine, path_text)
    if engine.property_store.get(key) is None:
        print(
            f"Error: {key} does not exist -- use 'prop add' to create it",
            file=sys.stderr,
        )
        return 1
    try:
        value = parse_property_value(value_token, named_ranks=named_ranks)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    code = _write(engine, key, value)
    if code is not None:
        return code
    print(f"updated {key} = {value!r}")
    return 0


def cmd_add(
    engine, path_text: str, value_token: str,
    named_ranks: Optional[Mapping[str, int]] = None,
) -> int:
    """``prop add <path> <value>`` -- STRICT: the key must NOT exist."""
    key = _canonical(engine, path_text)
    if engine.property_store.get(key) is not None:
        print(
            f"Error: {key} already exists -- use 'prop set' to change it",
            file=sys.stderr,
        )
        return 1
    try:
        value = parse_property_value(value_token, named_ranks=named_ranks)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    code = _write(engine, key, value)
    if code is not None:
        return code
    print(f"added {key} = {value!r}")
    return 0


def cmd_delete(engine, path_text: str) -> int:
    """``prop delete <path>`` -- the ONLY deletion spelling (no sugar)."""
    key = _canonical(engine, path_text)
    if engine.property_store.delete(key):
        print(f"deleted {key}")
        return 0
    print(f"{key} is not set")
    return 1


def cmd_upsert(
    engine, path_text: str, value_token: Optional[str] = None,
    named_ranks: Optional[Mapping[str, int]] = None,
) -> int:
    """The SUGAR entry (``dz .note`` / ``dz .note "hi"``): no value ->
    get; value -> upsert that ECHOES added-vs-updated (self-diagnosing
    typos). Reuses the same store + validation paths as the verbs."""
    if value_token is None:
        return cmd_get(engine, path_text)
    key = _canonical(engine, path_text)
    existed = engine.property_store.get(key) is not None
    try:
        value = parse_property_value(value_token, named_ranks=named_ranks)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    code = _write(engine, key, value)
    if code is not None:
        return code
    if existed:
        print(f"updated {key} = {value!r}")
    else:
        print(f"added {key} = {value!r} (new)")
    return 0


def cmd_assign(
    engine, path_text: str, rhs_text: str,
    named_ranks: Optional[Mapping[str, int]] = None,
) -> int:
    """The ``=`` assignment marker (``dz level=kit`` / ``dz .note="a b"``
    / ``dz .note=`` -- the kit-family DWP, 2026-07-03). One shell token,
    split by the caller at the FIRST ``=``; the RHS is opaque text. An
    EMPTY RHS sets the empty string (the one value the space form cannot
    express -- PS 5.1 drops bare ``""``). Same canonicalization,
    validation, and added/updated echo as every other write."""
    key = _canonical(engine, path_text)
    existed = engine.property_store.get(key) is not None
    if rhs_text == "":
        value: Any = ""
    else:
        try:
            value = parse_property_value(rhs_text, named_ranks=named_ranks)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    code = _write(engine, key, value)
    if code is not None:
        return code
    if existed:
        print(f"updated {key} = {value!r}")
    else:
        print(f"added {key} = {value!r} (new)")
    return 0


def cmd_list(engine, path_text: Optional[str] = None) -> int:
    """The listing form (``dz :.`` / ``dz :.kit:.`` / ``prop list``):
    boundary-aware family listing of a node's plane -- properties and
    fibers together. ``path_text=None`` lists the whole root."""
    if path_text is None:
        key = engine.command
    else:
        key = _canonical(engine, path_text, apply_value_alias=False)
    # 2f: a listing shows the DERIVED STRUCTURE alongside stored keys --
    # a real-but-empty container is no longer textually identical to a
    # non-container (the sweep's Finding-1 residue). Tree-needing path;
    # networkx loads lazily here only.
    structure = []
    try:
        from dazzlecmd_lib.fqcn_tree import build_tree, resolve_path
        tree = build_tree(engine.command)
        node_key = resolve_path(tree, key)
        if node_key in tree:
            kids = sorted(
                tree.successors(node_key),
                key=lambda n: (tree.nodes[n].get("rank") is None,
                               tree.nodes[n].get("rank", 0), n))
            for child in kids:
                kn = tree.nodes[child]
                rank = f" (rank {kn['rank']})" if "rank" in kn else ""
                structure.append(
                    f"    {child.rsplit(':', 1)[-1]:<14}"
                    f"{kn.get('kind', '')}{rank}")
    except Exception:
        pass  # structure display must never break the store listing
    if structure:
        print(f"{key} -- structure:")
        for line in structure:
            print(line)
    family = engine.property_store.list_prefix(key)
    if not family:
        if structure:
            print("(no properties set)")
        else:
            print(f"no properties set under {key}")
        return 0
    if structure:
        print("properties:")
    for k in sorted(family):
        print(f"{k} = {family[k]!r}")
    return 0
