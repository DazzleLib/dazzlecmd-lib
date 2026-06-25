"""B4 -- ``AggregatorEngine.resolve_target`` + the P-2 cross-level collision
policy (SD-1, AC1-1..1-8).

Builds a REAL engine with an isolated ``config_dir`` (no access to the user's
config), a REAL ``FQCNIndex`` for the tool tier (so the reuse in AC1-8 is
genuine, not mocked), real ``engine.kits`` for the kit tier, and ``self.name``
for the aggregator tier.
"""
import pytest

from dazzlecmd_lib.engine import AggregatorEngine
from dazzlecmd_lib.target_resolution import (
    LEVELS,
    AmbiguousLevelError,
    TargetResolution,
)
from dazzlecmd_lib.testing import make_tool, make_kit


def _tool(name, namespace="core"):
    return make_tool(
        name=name, namespace=namespace, _fqcn=f"{namespace}:{name}",
        short_name=name, kit_import_name=namespace)


def _engine(tmp_path, tools=(), kits=(), favorites=None):
    eng = AggregatorEngine(name="dz", command="dz", config_dir=str(tmp_path))
    for t in tools:
        eng.fqcn_index.insert_canonical(t)
    eng.kits = list(kits)
    if favorites:
        eng.config.write({"favorites": favorites})
    return eng


def test_levels_are_the_three_entity_types():
    assert LEVELS == ("tool", "kit", "aggregator")


class TestSingleLevel:
    def test_tool_only(self, tmp_path):                      # AC1-1 + AC1-8
        eng = _engine(tmp_path, tools=[_tool("solo")])
        r = eng.resolve_target("solo")
        assert isinstance(r, TargetResolution)
        assert r.level == "tool" and r.entity.name == "solo"
        assert r.tool_context is not None     # the FQCNIndex ctx is carried through

    def test_kit_only(self, tmp_path):
        eng = _engine(tmp_path, kits=[make_kit(name="mykit")])
        r = eng.resolve_target("mykit")
        assert r.level == "kit"
        assert (r.entity.kit_name or r.entity.name) == "mykit"

    def test_aggregator_self(self, tmp_path):
        eng = _engine(tmp_path)
        r = eng.resolve_target("dz")
        assert r.level == "aggregator" and r.entity is eng

    def test_unknown_returns_none(self, tmp_path):           # AC1-7
        eng = _engine(tmp_path, tools=[_tool("x")])
        assert eng.resolve_target("nope") is None


class TestAppliesAtPrunes:                                   # AC1-2
    def test_kit_scope_ignores_a_tool(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("foo")])
        assert eng.resolve_target("foo", applies_at=frozenset({"kit"})) is None

    def test_kit_scope_finds_the_kit(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("foo")], kits=[make_kit(name="foo")])
        r = eng.resolve_target("foo", applies_at=frozenset({"kit"}))
        assert r.level == "kit"


class TestCollisionReadAutoPick:                             # AC1-5
    def test_read_picks_tool_and_notifies(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("dup")], kits=[make_kit(name="dup")])
        r = eng.resolve_target("dup")                        # read (mutating=False)
        assert r.level == "tool"                             # tool > kit precedence
        assert r.notification and "kit" in r.notification    # names the other match
        assert {lvl for lvl, _ in r.candidates} == {"tool", "kit"}


class TestCollisionMutateFailLoud:                           # AC1-6
    def test_mutate_raises_and_changes_nothing(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("dup")], kits=[make_kit(name="dup")])
        with pytest.raises(AmbiguousLevelError) as ei:
            eng.resolve_target("dup", mutating=True)
        assert {lvl for lvl, _ in ei.value.candidates} == {"tool", "kit"}
        assert "--as" in str(ei.value)


class TestExplicitWins:                                      # AC1-3
    def test_as_level_pins_kit_over_tool(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("dup")], kits=[make_kit(name="dup")])
        r = eng.resolve_target("dup", as_level="kit")
        assert r.level == "kit" and r.notification is None

    def test_as_level_pins_even_for_a_mutating_verb(self, tmp_path):
        eng = _engine(tmp_path, tools=[_tool("dup")], kits=[make_kit(name="dup")])
        r = eng.resolve_target("dup", as_level="tool", mutating=True)
        assert r.level == "tool"            # pinned -> no AmbiguousLevelError


class TestFavoritePins:                                      # AC1-4
    def test_favorite_pins_the_tool_level(self, tmp_path):
        eng = _engine(
            tmp_path, tools=[_tool("dup")], kits=[make_kit(name="dup")],
            favorites={"dup": "core:dup"})
        r = eng.resolve_target("dup")        # read, but the favorite pins -> tool
        assert r.level == "tool" and r.notification is None
