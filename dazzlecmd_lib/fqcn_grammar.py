"""The FQCN path-operator grammar -- the bang-path parser (SD-FQCN-1).

Parses a canonical bang-path string into an ordered sequence of
operator + segment steps over the one unified FQCN tree. Four operators
map the three directions of the containment continuum plus the property
plane:

    :    path operator  -- lateral (the current level / a contained entity)
    .    property       -- inward (a property / user-exposed fiber)
    :.   fiber-path     -- inward (the internal mechanism plane; indivisible)
    :+   supra          -- outward / up (the higher continua; indivisible)

Segment names are ``[a-z0-9][a-z0-9_-]*`` (no ``.`` -- locked #77). The
leading token is the root (e.g. ``dz`` / ``dazzlecmd``). Tokenizing is
COMPOUND-FIRST: ``:.`` and ``:+`` are matched before a bare ``:`` so they
are never mis-split into ``:`` + ``.`` / ``:`` + ``+``.

This module is pure string<->structure: it does NOT resolve a path to a
node (that is the resolver's job, a later slice). ``parse`` gives the
structure; ``unparse`` round-trips it back to the canonical string.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional, Tuple


# Operators. ``_OPERATORS`` is COMPOUND-FIRST: the two-char compounds are
# tested before the bare ``:`` so ``:.`` / ``:+`` win at a shared prefix.
OP_PATH = ":"
OP_PROP = "."
OP_FIBER = ":."
OP_SUPRA = ":+"
_OPERATORS = (OP_FIBER, OP_SUPRA, OP_PATH, OP_PROP)

_NAME = r"[a-z0-9][a-z0-9_-]*"
_NAME_RE = re.compile(_NAME)
# Sub-key segments -- a ':'-step INSIDE the property plane -- address INTO
# the property's JSON value (the USER'S data, not our tree), so they are
# CASE-PRESERVING: ``dz.env-vars:DEBUG`` != ``dz.env-vars:debug``. Tree
# segments (entities, fibers, property names) stay lowercase (v2 contract
# R1.5; amends SD-FQCN-1 V12).
_SUBKEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")


class FQCNParseError(ValueError):
    """Raised when a bang-path string is not well-formed."""


class Segment(NamedTuple):
    """One step in a bang-path: the operator that PRECEDES ``name``."""

    op: str  # OP_PATH / OP_PROP / OP_FIBER / OP_SUPRA
    name: str


class ParsedPath(NamedTuple):
    """A parsed bang-path: a ``root`` token + ordered ``segments``."""

    root: str
    segments: Tuple[Segment, ...]


def _match_operator(path: str, pos: int) -> Optional[str]:
    """Return the operator at ``pos`` (compound-first), or None."""
    for op in _OPERATORS:
        if path.startswith(op, pos):
            return op
    return None


def parse(path: str, implicit_root: Optional[str] = None) -> ParsedPath:
    """Parse a canonical bang-path string into a :class:`ParsedPath`.

    Args:
        path: The bang-path text.
        implicit_root: When given and ``path`` STARTS with an operator
            (``.note``, ``:.kit``, ``:+kit``), this root is prepended --
            the CLI's root-elision sugar. The value is the RUNNING
            aggregator's root (``engine.command`` -- SELF-rooted, never a
            hardcoded ``dz``; v2 contract R1.2). No effect on paths that
            already carry a root.

    Grammar planes (v2 contract R1.5): tree segments (entities, fibers,
    property names) are lowercase; once the path enters the PROPERTY
    plane (the first ``.`` step), a ``:`` step becomes a CASE-PRESERVING
    sub-key into the property's value, and ``:.``/``:+`` become illegal
    (a property has no fiber/supra plane).

    Raises:
        FQCNParseError: on a malformed string -- a bad/missing root, an
            operator with no following name, a trailing operator, a double
            operator, an illegal character, or a fiber/supra step inside
            the property plane.
    """
    if not isinstance(path, str) or not path:
        raise FQCNParseError(f"empty or non-string path: {path!r}")

    if implicit_root is not None and _match_operator(path, 0) is not None:
        path = implicit_root + path

    m = _NAME_RE.match(path)
    if m is None or m.start() != 0:
        raise FQCNParseError(f"path must start with a root name: {path!r}")
    root = m.group(0)
    pos = m.end()

    segments = []
    in_property = False  # flips at the first '.' step; never flips back
    while pos < len(path):
        op = _match_operator(path, pos)
        if op is None:
            raise FQCNParseError(
                f"expected an operator (':' '.' ':.' ':+') at index "
                f"{pos}: {path!r}"
            )
        if in_property and op in (OP_FIBER, OP_SUPRA):
            raise FQCNParseError(
                f"{op!r} is not allowed inside the property plane (a "
                f"property has no fiber/supra plane) at index {pos}: "
                f"{path!r}"
            )
        pos += len(op)
        name_re = _SUBKEY_RE if (in_property and op == OP_PATH) else _NAME_RE
        nm = name_re.match(path, pos)
        if nm is None or nm.start() != pos:
            raise FQCNParseError(
                f"expected a segment name after {op!r} at index "
                f"{pos}: {path!r}"
            )
        segments.append(Segment(op, nm.group(0)))
        pos = nm.end()
        if op == OP_PROP:
            in_property = True

    return ParsedPath(root, tuple(segments))


def unparse(parsed: ParsedPath) -> str:
    """Serialize a :class:`ParsedPath` back to its canonical string."""
    out = [parsed.root]
    for seg in parsed.segments:
        out.append(seg.op)
        out.append(seg.name)
    return "".join(out)


def is_bangpath(path: str) -> bool:
    """True if ``path`` parses as a well-formed bang-path."""
    try:
        parse(path)
        return True
    except FQCNParseError:
        return False


def is_operator_led(text: str) -> bool:
    """True if ``text`` begins with one of the four path operators.

    The CLI's first routing question (v2 contract R1.1 step 5): an
    operator-led first token is a path expression, never a tool/verb name.
    """
    return isinstance(text, str) and _match_operator(text, 0) is not None


def parse_cli(
    text: str, implicit_root: Optional[str] = None
) -> Tuple[ParsedPath, Optional[str]]:
    """Parse a CLI path token: a bang-path with an optional TRAILING
    listing operator (v2 contract R1.6).

    A trailing bare ``:.`` is split off BEFORE parsing (the C-5 order --
    so ``dz .env-vars:.`` splits to the property path + listing rather
    than false-positive as an interior fiber op) and returned as
    ``trailing_op``. Semantics of ``trailing_op == ':.'``: list the
    node's plane (properties and fibers together).

    A trailing bare ``.``, ``:`` or ``:+`` is RESERVED -- error with a
    hint. An empty prefix (``:.`` alone) lists the implicit root itself.

    Returns:
        ``(parsed_path, trailing_op)`` where ``trailing_op`` is ``':.'``
        or ``None``.
    """
    if not isinstance(text, str) or not text:
        raise FQCNParseError(f"empty or non-string path: {text!r}")

    trailing = None
    if text.endswith(OP_FIBER):
        trailing = OP_FIBER
        text = text[: -len(OP_FIBER)]
    elif text.endswith(OP_SUPRA):
        raise FQCNParseError(
            f"trailing {OP_SUPRA!r} is reserved (supra navigation lands "
            f"with SD-7): {text!r}"
        )
    elif text.endswith(OP_PATH) or text.endswith(OP_PROP):
        raise FQCNParseError(
            f"trailing {text[-1]!r} is reserved -- did you mean a "
            f"trailing ':.' (plane listing)?: {text!r}"
        )

    if not text:
        # bare ':.' -- list the (implicit) root itself
        if implicit_root is None:
            raise FQCNParseError(
                "a bare ':.' needs an implicit root (the running "
                "aggregator)"
            )
        return ParsedPath(implicit_root, ()), trailing

    return parse(text, implicit_root=implicit_root), trailing


# --------------------------------------------------------------------------
# Canonicalization (v2 contract R1.3) -- the first-segment FIBER_ROOTS rule.
# --------------------------------------------------------------------------

# The fiber-plane vocabulary that EXISTS today -- the interim registry for
# canonicalization until SD-FQCN-2 derives the real tree (then this becomes
# a derived view; a name ENTERING this set later needs `dz meta prop
# migrate` to re-canonicalize forgiven keys -- ledger'd). Sources: the
# verb_axis vocabulary (axes + levels-as-machinery) + `meta` (the canonical
# verb namespace). HISTORY: ``level`` was originally EXCLUDED (C-3, so
# `:.level` forgave to the property) -- RETIRED 2026-07-04 when 2d made the
# axis node real (the canonicalization-identity invariant caught the axis
# being unaddressable); the property keeps its three spellings and the
# axis node's bare VALUE aliases to it (prop_commands.NODE_VALUE_ALIASES).
FIBER_ROOTS = frozenset({
    "kit", "tool", "aggregator",           # levels-as-machinery (the flagship `:.kit.channels...`)
    "verb", "meta",                        # the verb space + the meta verb namespace
    "channels",                            # the output layer node
    "management", "activation", "loading", # the verb axes
    "membership", "projection", "visibility", "mode",
    "level",   # joined 2026-07-04: the axis node is REAL post-2d (the
               # invariant caught bare ':.level' forgiving away, making
               # the axis unaddressable). The property keeps its three
               # spellings (dz level / dz .level / level=); the C-3
               # muscle-memory case retires in favor of a near-miss HINT
               # (the hintlib seed).
})


def canonicalize(
    text: str,
    implicit_root: Optional[str] = None,
    fiber_roots: frozenset = FIBER_ROOTS,
) -> Tuple[str, bool]:
    """Return ``(canonical_text, was_forgiven)`` for a path token.

    THE RULE (first-segment FIBER_ROOTS; v2 contract R1.3): a ``:.``-led
    path whose FIRST segment is NOT fiber-plane vocabulary is the user's
    muscle-memory spelling of a PROPERTY path -- the WHOLE path forgives
    to the property plane by rewriting only the leading ``:.`` to ``.``
    (``:.note.author`` -> ``.note.author``; sub-keys preserved:
    ``:.env-vars:DEBUG`` -> ``.env-vars:DEBUG``). A first segment IN
    ``fiber_roots`` passes verbatim (``:.kit.channels.verbosity``).
    Interior ``:.``/``:+`` after a forgiven first segment ERROR on the
    re-parse (the property plane rejects them -- don't guess).

    The caller ECHOES the canonical form when ``was_forgiven`` (AC-11:
    the whole rewritten path, case untouched).

    Forgiveness happens at the TEXT level, BEFORE parsing: the
    un-forgiven spelling may be unparseable precisely because it is in
    the wrong plane (``:.env-vars:DEBUG`` -- an uppercase sub-key is only
    legal after a ``.`` step, so the fiber spelling cannot parse). The
    rewrite moves the path into the property plane; the subsequent parse
    then enforces that plane's rules on the remainder (interior
    ``:.``/``:+`` error -- don't guess).

    Returns canonical TEXT (root included when implicit_root supplied).
    """
    if not isinstance(text, str) or not text:
        raise FQCNParseError(f"empty or non-string path: {text!r}")

    if implicit_root is not None and _match_operator(text, 0) is not None:
        text = implicit_root + text

    # A path whose FIRST step is ':.' with a first segment OUTSIDE the
    # fiber vocabulary forgives to the property plane (leading ':.' -> '.').
    m = re.match(rf"({_NAME}){re.escape(OP_FIBER)}({_NAME})", text)
    if m is not None and m.group(2) not in fiber_roots:
        forgiven_text = m.group(1) + OP_PROP + text[m.end(1) + len(OP_FIBER):]
        parsed = parse(forgiven_text)  # may raise: interior :./:+ illegal
        return unparse(parsed), True

    parsed = parse(text)
    return unparse(parsed), False


# The planes a segment can live on (see segment_planes).
PLANE_ENTITY = "entity"
PLANE_FIBER = "fiber"
PLANE_SUPRA = "supra"
PLANE_PROPERTY = "property"
PLANE_SUBKEY = "subkey"


def segment_planes(parsed: ParsedPath) -> Tuple[str, ...]:
    """Classify each segment of ``parsed`` by the plane it addresses.

    ``:`` -> entity (or SUBKEY once inside the property plane); ``:.`` ->
    fiber; ``:+`` -> supra; ``.`` -> property. Derivable from the ops
    alone (no registry needed) -- the parser is registry-independent.
    """
    planes = []
    in_property = False
    for seg in parsed.segments:
        if seg.op == OP_PROP:
            in_property = True
            planes.append(PLANE_PROPERTY)
        elif seg.op == OP_PATH:
            planes.append(PLANE_SUBKEY if in_property else PLANE_ENTITY)
        elif seg.op == OP_FIBER:
            planes.append(PLANE_FIBER)
        else:  # OP_SUPRA
            planes.append(PLANE_SUPRA)
    return tuple(planes)
