"""Tests for ``MODE_SPACE`` -- mode as a ContinuumSpace (SD-2, slice 1).

Mode is ``materialization`` (a 3-rung PRESENCE Continuum) x ``upstream`` (a binary
PROVENANCE Groupable). The flat user-facing pick {symlink, submodule, embedded,
local-only} is the GROUPED projection of that 2-axis space; ``axes_for_mode`` /
``mode_for_axes`` are the ungroup/group bridge. These tests pin:

- the presence ORDER (embedded is more present than symlink is more present than
  absent) -- the "embedded IS more present" adjudication (FINAL_ASSESSMENT Add. 2);
- ``upstream`` is orthogonal/binary, NOT presence;
- the flat-enum <-> axes round-trip, including the MISSING fiber boundary;
- the 2x2 grid is a derived ``quadrants(materialization, upstream)`` view.
"""

from __future__ import annotations

import pytest

from dazzlecmd_lib.mode import (
    MATERIALIZATION_CONTINUUM,
    MATERIALIZED_ABSENT,
    MATERIALIZED_EMBODIED,
    MATERIALIZED_REFERENCED,
    MODE_COORDINATES,
    MODE_SPACE,
    STATE_EMBEDDED,
    STATE_LOCAL_ONLY,
    STATE_MISSING,
    STATE_SUBMODULE,
    STATE_SYMLINK,
    UPSTREAM_CONTINUUM,
    UPSTREAM_TRACKED,
    UPSTREAM_UNTRACKED,
    axes_for_mode,
    mode_for_axes,
)
from dazzlecmd_lib.verb_axis import MODE_APPLIES_AT, VERB_SPACE

MATERIALIZED_STATES = [STATE_SUBMODULE, STATE_EMBEDDED, STATE_SYMLINK,
                       STATE_LOCAL_ONLY]


class TestMaterializationIsPresence:
    """The materialization axis is a graded presence Continuum, embodied warmest."""

    def test_three_rungs_ordered_embodied_referenced_absent(self):
        c = MATERIALIZATION_CONTINUUM
        # cold -> warm
        assert c.levels() == (MATERIALIZED_ABSENT, MATERIALIZED_REFERENCED,
                              MATERIALIZED_EMBODIED)
        assert c.warm_pole() == MATERIALIZED_EMBODIED
        assert c.cold_pole() == MATERIALIZED_ABSENT
        assert c.neutral() == MATERIALIZED_EMBODIED  # most-present = the invariant 0

    def test_embodied_is_more_present_than_referenced(self):
        # The adjudicated point: a real directory (the thing) is MORE present than
        # a symlink (a level of indirection to it).
        assert MATERIALIZATION_CONTINUUM.is_warmer(
            MATERIALIZED_EMBODIED, MATERIALIZED_REFERENCED)

    def test_referenced_is_more_present_than_absent(self):
        # A symlink (a pointer that resolves) is more present than nothing.
        assert MATERIALIZATION_CONTINUUM.is_warmer(
            MATERIALIZED_REFERENCED, MATERIALIZED_ABSENT)


class TestUpstreamIsBinaryProvenance:
    """The upstream axis is a binary {tracked, untracked} Groupable, not presence."""

    def test_binary(self):
        assert set(UPSTREAM_CONTINUUM.levels()) == {UPSTREAM_TRACKED,
                                                    UPSTREAM_UNTRACKED}
        assert UPSTREAM_CONTINUUM.warm_pole() == UPSTREAM_TRACKED


class TestModeSpace:
    """MODE_SPACE = materialization x upstream, an independent product."""

    def test_two_axes(self):
        assert set(MODE_SPACE.axes) == {"materialization", "upstream"}

    def test_quadrants_view_over_the_two_axes(self):
        # The 2x2 grid the flat names form is a derived pairwise view, not a
        # structural limit -- it is one PROJECTION of the space.
        qv = MODE_SPACE.quadrants("materialization", "upstream")
        assert qv.axis1 == "materialization"
        assert qv.axis2 == "upstream"
        assert len(qv.quadrants()) == 4


class TestFlatEnumAxesBridge:
    """The flat pick (grouped) <-> (materialization, upstream) (ungrouped)."""

    @pytest.mark.parametrize("state", MATERIALIZED_STATES)
    def test_round_trip_for_materialised_states(self, state):
        materialization, upstream = axes_for_mode(state)
        assert mode_for_axes(materialization, upstream) == state

    def test_known_coordinates(self):
        assert axes_for_mode(STATE_SUBMODULE) == (MATERIALIZED_EMBODIED,
                                                  UPSTREAM_TRACKED)
        assert axes_for_mode(STATE_EMBEDDED) == (MATERIALIZED_EMBODIED,
                                                 UPSTREAM_UNTRACKED)
        assert axes_for_mode(STATE_SYMLINK) == (MATERIALIZED_REFERENCED,
                                                UPSTREAM_TRACKED)
        assert axes_for_mode(STATE_LOCAL_ONLY) == (MATERIALIZED_REFERENCED,
                                                   UPSTREAM_UNTRACKED)

    def test_local_only_equals_embedded_modulo_presence(self):
        # The user's "essentially the same, subtle difference": same provenance
        # (both untracked), differ ONLY on the presence rung.
        _, u_local = axes_for_mode(STATE_LOCAL_ONLY)
        _, u_embedded = axes_for_mode(STATE_EMBEDDED)
        assert u_local == u_embedded == UPSTREAM_UNTRACKED
        m_local, _ = axes_for_mode(STATE_LOCAL_ONLY)
        m_embedded, _ = axes_for_mode(STATE_EMBEDDED)
        assert m_local != m_embedded  # referenced vs embodied -- the only difference

    def test_missing_is_the_absent_fiber_boundary(self):
        materialization, upstream = axes_for_mode(STATE_MISSING)
        assert materialization == MATERIALIZED_ABSENT
        assert upstream is None  # no tracking when unmaterialised

    def test_absent_groups_to_missing_regardless_of_upstream(self):
        assert mode_for_axes(MATERIALIZED_ABSENT, None) == STATE_MISSING
        assert mode_for_axes(MATERIALIZED_ABSENT, UPSTREAM_TRACKED) == STATE_MISSING

    def test_unknown_state_raises(self):
        with pytest.raises(KeyError):
            axes_for_mode("not-a-mode")

    def test_unknown_coordinate_raises(self):
        with pytest.raises(KeyError):
            mode_for_axes(MATERIALIZED_EMBODIED, "not-an-upstream")


def test_every_state_constant_has_a_coordinate():
    # Guard against a new STATE_* being added without a coordinate mapping.
    assert set(MODE_COORDINATES) == {
        STATE_SUBMODULE, STATE_EMBEDDED, STATE_SYMLINK, STATE_LOCAL_ONLY,
        STATE_MISSING,
    }


class TestModeInVerbSpace:
    """Mode is now a MEMBER of the one MUTATE space -- the dz mode + dz kit
    management unification, made structural. Mode joins as a nested SUBSPACE
    (not a flat binary VerbAxis) alongside activation/loading/membership/projection.
    """

    def test_mode_is_a_member_of_verb_space(self):
        assert "mode" in VERB_SPACE.axes

    def test_mode_sits_alongside_the_lifecycle_axes(self):
        # The unification: kit-management axes and mode in ONE space.
        assert {"activation", "loading", "membership", "projection", "mode"} \
            <= set(VERB_SPACE.axes)

    def test_the_mode_member_carries_both_sub_axes(self):
        mode_member = VERB_SPACE.axes["mode"]
        assert set(mode_member.axes) == {"materialization", "upstream"}

    def test_mode_applies_at_every_real_level(self):
        # Mode is NOT tool-only (the historical silo) -- it spans the continuum.
        assert MODE_APPLIES_AT == frozenset({"tool", "kit", "aggregator"})
