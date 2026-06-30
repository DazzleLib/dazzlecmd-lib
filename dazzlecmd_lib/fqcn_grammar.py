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


def parse(path: str) -> ParsedPath:
    """Parse a canonical bang-path string into a :class:`ParsedPath`.

    Raises:
        FQCNParseError: on a malformed string -- a bad/missing root, an
            operator with no following name, a trailing operator, a double
            operator, or an illegal character.
    """
    if not isinstance(path, str) or not path:
        raise FQCNParseError(f"empty or non-string path: {path!r}")

    m = _NAME_RE.match(path)
    if m is None or m.start() != 0:
        raise FQCNParseError(f"path must start with a root name: {path!r}")
    root = m.group(0)
    pos = m.end()

    segments = []
    while pos < len(path):
        op = _match_operator(path, pos)
        if op is None:
            raise FQCNParseError(
                f"expected an operator (':' '.' ':.' ':+') at index "
                f"{pos}: {path!r}"
            )
        pos += len(op)
        nm = _NAME_RE.match(path, pos)
        if nm is None or nm.start() != pos:
            raise FQCNParseError(
                f"expected a segment name after {op!r} at index "
                f"{pos}: {path!r}"
            )
        segments.append(Segment(op, nm.group(0)))
        pos = nm.end()

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
