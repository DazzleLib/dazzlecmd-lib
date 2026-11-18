"""The scopes of ``--yes``: what, exactly, the caller is saying yes to.

``--yes`` is the one gate. Alone it means ``noninteractive`` -- "do not
prompt; I have decided". It can carry a comma-separated list of scopes, each
an explicit permission:

    noninteractive            proceed without a prompt (what bare --yes means)
    dangerous                 reserved for ``dz guarded``: run a destructive
                              command that has no safe form (gates nothing
                              inside safedel itself)
    delete-through-junction   delete what a junction points at, not only the link
    delete-through-symlink    delete what a symlink points at, not only the link
    copy-first                make a hash-verified copy before moving (force on)
    no-copy-first             stage without that extra copy (force off)

Any ``--yes`` consents to proceeding without a prompt.

The parsing hazard is that ``--yes`` takes an OPTIONAL value, and the next
word on the command line is usually a path. ``--yes somefile`` must never
read ``somefile`` as scopes. The rule: the word after ``--yes`` is a scope
list only when EVERY comma-separated part is a known scope name; otherwise
it is left alone as a path. A file that is literally named like a scope is
reached as ``./dangerous`` or after ``--``. A list that mixes known and
unknown names (``dangerous,typo``) is a mistake, not a path, and is reported.

Stdlib only.
"""

from typing import List, Tuple

NONINTERACTIVE = "noninteractive"
DANGEROUS = "dangerous"
DELETE_THROUGH_JUNCTION = "delete-through-junction"
DELETE_THROUGH_SYMLINK = "delete-through-symlink"
COPY_FIRST = "copy-first"
NO_COPY_FIRST = "no-copy-first"

SCOPES = (
    NONINTERACTIVE,
    DANGEROUS,
    DELETE_THROUGH_JUNCTION,
    DELETE_THROUGH_SYMLINK,
    COPY_FIRST,
    NO_COPY_FIRST,
)


class ScopeError(ValueError):
    """A ``--yes`` value that is neither a scope list nor a path."""


def parse_scopes(text: str) -> Tuple[str, ...]:
    """'a,b' -> ('a', 'b'), validated. Empty text means bare ``--yes``.

    Raises ScopeError for an unknown name or a contradictory pair.
    """
    if text is None or str(text).strip() == "":
        return (NONINTERACTIVE,)
    parts = [p.strip().lower() for p in str(text).split(",")]
    unknown = [p for p in parts if p not in SCOPES]
    if unknown:
        raise ScopeError(
            "unknown --yes scope(s): %s. Known scopes: %s"
            % (", ".join(repr(u) for u in unknown), ", ".join(SCOPES))
        )
    out: List[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    if COPY_FIRST in out and NO_COPY_FIRST in out:
        raise ScopeError("--yes cannot carry both copy-first and no-copy-first")
    return tuple(out)


def _looks_like_scope_list(word: str) -> str:
    """'all' if every comma part is a scope, 'some' if only some are,
    'none' otherwise. Case-insensitive."""
    if not word or word.startswith("-"):
        return "none"
    parts = [p.strip().lower() for p in word.split(",")]
    known = [p in SCOPES for p in parts]
    if all(known):
        return "all"
    # A comma list where at least one part is a scope is a typo, not a path:
    # paths that contain a comma AND a scope name as a whole part are rare
    # enough that asking for ./name is the right answer.
    if any(known) and len(parts) > 1:
        return "some"
    return "none"


def split_yes(argv: List[str]) -> Tuple[List[str], Tuple[str, ...], bool]:
    """Take ``--yes`` / ``-y`` (and any scope list) out of an argument list.

    Returns ``(remaining argv, scopes, yes_given)``. ``scopes`` is empty when
    ``--yes`` was not given. Arguments after a bare ``--`` are never examined:
    they are paths.

    Raises ScopeError for ``--yes=<bad list>`` and for a following word that
    mixes known and unknown scope names.
    """
    out: List[str] = []
    scopes: List[str] = []
    yes_given = False
    i = 0
    n = len(argv)
    while i < n:
        arg = argv[i]
        if arg == "--":
            out.extend(argv[i:])
            break
        value = None
        matched = False
        if arg in ("--yes", "-y"):
            matched = True
            nxt = argv[i + 1] if i + 1 < n else None
            if nxt is not None:
                kind = _looks_like_scope_list(nxt)
                if kind == "all":
                    value = nxt
                    i += 1
                elif kind == "some":
                    raise ScopeError(
                        "--yes %s: not every name is a known scope (%s). "
                        "If %r is a path, write it as ./%s or put it after --."
                        % (nxt, ", ".join(SCOPES), nxt, nxt)
                    )
        elif arg.startswith("--yes="):
            matched = True
            value = arg[len("--yes="):]
            if value == "":
                raise ScopeError("--yes= needs a scope list, or use bare --yes")
        if matched:
            yes_given = True
            for s in parse_scopes(value if value is not None else ""):
                if s not in scopes:
                    scopes.append(s)
        else:
            out.append(arg)
        i += 1
    if COPY_FIRST in scopes and NO_COPY_FIRST in scopes:
        raise ScopeError("--yes cannot carry both copy-first and no-copy-first")
    return out, tuple(scopes), yes_given
