"""Value-token parsing for the property surface (v2 contract R1.4).

The value side of ``dz .kit.channels.verbosity <value>`` / ``dz meta prop
set <path> <value>``. Path grammar lives in :mod:`fqcn_grammar`; this
module owns the VALUE domain: negative-number acceptance (pinned to
argparse's own matcher so the sugar and the explicit form are symmetric),
counted verbosity forms (``vvvv`` / ``qqq``), ints/floats, JSON literals,
and optional named-rank resolution (the THAC0 rung names, supplied by the
caller -- this lib does not import log_lib).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional


# argparse's OWN negative-number matcher (CPython 3.12
# ``ArgumentParser._negative_number_matcher``), pinned here so the sugar
# path accepts exactly what the explicit argparse path accepts -- the
# collabN review showed a hand-rolled regex re-creates the asymmetry
# (argparse accepts ``-.5``). If CPython changes the internal matcher, the
# CI vectors in tests/test_property_values.py surface it as a test failure
# instead of silent drift. Scientific notation / ``-inf`` need ``--`` on
# BOTH surfaces (documented, symmetric).
_NEGATIVE_NUMBER_RE = re.compile(r"^-\d+$|^-\d*\.\d+$")

_COUNTED_V_RE = re.compile(r"^v+$")   # vvvv -> +4
_COUNTED_Q_RE = re.compile(r"^q+$")   # qqq  -> -3
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?(\d+\.\d*|\d*\.\d+)$")


def is_negative_number_token(token: str) -> bool:
    """True iff ``token`` is a value by argparse's negative-number rule.

    Used by the sugar path to accept ``dz .kit.channels.verbosity -3``
    bare (no ``--``), exactly as the explicit ``prop set`` form does.
    """
    return isinstance(token, str) and _NEGATIVE_NUMBER_RE.match(token) is not None


def parse_property_value(
    token: str,
    named_ranks: Optional[Mapping[str, int]] = None,
) -> Any:
    """Parse a single value token into its stored (JSON-typed) value.

    Order: counted verbosity (``vvvv``=+4 / ``qqq``=-3) -> named rank
    (via ``named_ranks``, e.g. the THAC0 rung names supplied by the
    caller) -> int -> float -> JSON literal (``true``/``false``/``null``,
    quoted strings, ``[...]``/``{...}``) -> the raw string.

    Empty tokens are the CALLER'S error (R1.4: an empty value token on
    the CLI is an error hinting ``prop delete`` -- PS 5.1 drops ``""``
    entirely, so "set to empty" must never be load-bearing).
    """
    if not isinstance(token, str) or token == "":
        raise ValueError(
            "empty value token -- to remove a property use "
            "'prop delete'; to store an empty string, a future "
            "'prop set --empty' is the reserved spelling"
        )
    if _COUNTED_V_RE.match(token):
        return len(token)
    if _COUNTED_Q_RE.match(token):
        return -len(token)
    if named_ranks is not None and token in named_ranks:
        return named_ranks[token]
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    try:
        return json.loads(token)
    except (json.JSONDecodeError, ValueError):
        return token
