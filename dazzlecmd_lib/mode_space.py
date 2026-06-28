"""MODE_SPACE -- mode as a ContinuumSpace (the pure space; no git/subprocess I/O).

Lifted out of ``mode.py`` (the heavy git/subprocess implementation) so the verb
registry (``verb_axis.py``) can compose mode into the one MUTATE ``VERB_SPACE``
WITHOUT importing the implementation (H8: the lib names structure; handlers live
elsewhere). ``mode.py`` re-exports every name here, so existing
``from dazzlecmd_lib.mode import MODE_SPACE`` imports are byte-unchanged.

The model (FINAL_ASSESSMENT Addendum 2 / the H5' amendment; mode/H5 capstone DWP
``2026-06-27__21-46-59``):

The flat pick ``dz mode switch <name>`` over {symlink, submodule, embedded,
local-only} is the GROUPED projection -- ONE selectable line, which is exactly
what a human wants when choosing a mode. Underneath, each name is a POINT in
MODE_SPACE = materialization x upstream: the UNGROUPED decomposition the rest of
the CLI reasons against (info, cross-level, cascade). Same point, two faces --
``{grouping, ungrouping} = {P, not-P}`` applied to the mode names themselves.

  materialization (PRESENCE, graded): embodied > referenced > absent
      how much of the thing is HERE -- a real directory (the thing itself) is more
      present than a symlink (a level of indirection to it elsewhere) is more
      present than nothing (a #80 pointer / MISSING).
  upstream (PROVENANCE, binary): tracked vs untracked -- whether a remote (a git
      submodule) governs updates/push/pull. ORTHOGONAL to presence: a submodule
      dir and an embedded dir are equally present; they differ only in tracking.

No single 1-D order spans the four named modes -- you cannot linearise a
(presence-Continuum x orthogonal-binary) product. The 2x2 grid the names form is
``MODE_SPACE.quadrants("materialization", "upstream")``; the presence ladder is
the ``materialization`` axis read alone.
"""
from __future__ import annotations

from dazzlecmd_lib.continuum import Continuum, ContinuumSpace

# Presence rungs (warm = most present at rank 0; colder = less present).
MATERIALIZED_EMBODIED = "embodied"      # a real directory -- the thing in place
MATERIALIZED_REFERENCED = "referenced"  # a symlink/junction -- indirection to it
MATERIALIZED_ABSENT = "absent"          # no directory -- a #80 pointer / MISSING

MATERIALIZATION_CONTINUUM = Continuum(
    name="materialization",
    ranks={MATERIALIZED_EMBODIED: 0,
           MATERIALIZED_REFERENCED: -1,
           MATERIALIZED_ABSENT: -2},
)

# Provenance poles (binary; warm = tracked at 0).
UPSTREAM_TRACKED = "tracked"      # a git submodule governs updates/push/pull
UPSTREAM_UNTRACKED = "untracked"  # purely local, no remote

UPSTREAM_CONTINUUM = Continuum(
    name="upstream",
    ranks={UPSTREAM_TRACKED: 0, UPSTREAM_UNTRACKED: -1},
)

# A PRODUCT (independent axes, scale-safe): materialization is a presence
# dimension, upstream is orthogonal provenance -- no cross-axis "warmer/colder".
MODE_SPACE = ContinuumSpace.compose(
    "mode",
    {"materialization": MATERIALIZATION_CONTINUUM, "upstream": UPSTREAM_CONTINUUM},
    meaning="how a tracked entity is embodied (presence) x "
            "whether a remote governs it (provenance)",
)
