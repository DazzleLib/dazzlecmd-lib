"""Direct unit tests for the lib read surface (SD-A, `interrogation.py`).

The dazzlecmd consumer exercises this transitively through its kit/aggregator
cards, but the module deserves its own coverage in the library -- especially the
`facets=` reduction path, which has no CLI caller yet (it lands at SD-A slice 4).
"""
import json
import types

import pytest

from dazzlecmd_lib.interrogation import (
    Interrogation,
    Section,
    axis_state,
    interrogate,
    membership_rows,
    render_interrogation,
    structure_rows,
)


def _kit(name="demo", *, virtual=False, tools=(1, 2), version="1.0",
         always_active=False):
    return types.SimpleNamespace(
        kit_name=name, name=name, virtual=virtual, tools=list(tools),
        version=version, description="A demo kit.", kit_import_name=None,
        directory=None, kit_source="/x/demo.kit.json",
        always_active=always_active)


def _engine(*, kits=(), config=None):
    return types.SimpleNamespace(
        command="dz", name="dazzlecmd",
        description="A demo aggregator.", version_info=("1.2.3", "1.2.3-full"),
        kits=list(kits), _get_user_config=lambda: (config or {}))


def _tool(name="widget", *, directory=None, version="2.1", fqcn="core:widget",
          namespace="core"):
    return types.SimpleNamespace(
        name=name, fqcn=fqcn, namespace=namespace, kit_import_name=None,
        version=version, description="A demo tool.", platform=None,
        language="python", taxonomy={"category": "util", "tags": ["a", "b"]},
        directory=directory)


# --- axis_state: the state facet = the read-projection of VERB_AXES ---------


class TestAxisState:
    def test_present_kit_returns_the_three_axis_rows(self, tmp_path):
        kit = _kit("demo")
        rows, always = axis_state(kit, _engine(kits=[kit]), str(tmp_path))
        rungs = {axis: rung for axis, rung, _w, _c in rows}
        assert [axis for axis, *_ in rows] == [
            "activation", "loading", "membership"]
        assert rungs["activation"] == "active"
        assert rungs["loading"] == "loaded (attached)"
        assert rungs["membership"] == "member"
        assert always is False

    def test_absent_kit_returns_none(self, tmp_path):
        assert axis_state("ghost", _engine(kits=[]), str(tmp_path)) == (
            None, False)

    def test_disabled_kit_reads_disabled_on_activation(self, tmp_path):
        kit = _kit("demo")
        eng = _engine(kits=[kit], config={"disabled_kits": ["demo"]})
        rows, _ = axis_state(kit, eng, str(tmp_path))
        assert {a: r for a, r, *_ in rows}["activation"] == "disabled"

    def test_accepts_a_kit_name_string_or_an_entity(self, tmp_path):
        kit = _kit("demo")
        eng = _engine(kits=[kit])
        assert (axis_state(kit, eng, str(tmp_path))
                == axis_state("demo", eng, str(tmp_path)))


# --- interrogate: facet sections, including the reduction path --------------


class TestInterrogateKit:
    def test_full_view_has_identity_then_state(self, tmp_path):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", project_root=str(tmp_path))
        assert isinstance(interro, Interrogation) and interro.level == "kit"
        assert [s.name for s in interro.sections] == ["identity", "state"]
        identity, state = interro.sections
        assert identity.kind == "fields"
        assert identity.title == "Kit 'demo' -- identity card:"
        assert dict(identity.rows)["Kind"] == "kit"
        assert state.kind == "axes" and state.title == "Current state:"
        assert [a for a, *_ in state.rows] == [
            "activation", "loading", "membership"]

    def test_facet_identity_only_is_the_reduction(self, tmp_path):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", facets={"identity"},
            project_root=str(tmp_path))
        assert [s.name for s in interro.sections] == ["identity"]

    def test_facet_state_only_is_the_reduction(self, tmp_path):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", facets={"state"},
            project_root=str(tmp_path))
        assert [s.name for s in interro.sections] == ["state"]

    def test_virtual_kit_kind_and_alias_count(self, tmp_path):
        kit = _kit("v", virtual=True, tools=(1, 2, 3))
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", facets={"identity"},
            project_root=str(tmp_path))
        fields = dict(interro.sections[0].rows)
        assert fields["Kind"] == "virtual kit"
        assert fields["Tools"] == "3 alias(es)"

    def test_unset_version_normalizes_to_none(self, tmp_path):
        kit = _kit("demo", version="0.0.0")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", facets={"identity"},
            project_root=str(tmp_path))
        assert dict(interro.sections[0].rows)["Version"] is None


class TestInterrogateAggregator:
    def test_identity_only_no_state_facet(self):
        eng = _engine(kits=[])
        interro = interrogate(
            eng, eng, level="aggregator", projects=[1, 2, 3], kits=[])
        assert [s.name for s in interro.sections] == ["identity"]
        fields = dict(interro.sections[0].rows)
        assert interro.sections[0].title == "Aggregator 'dazzlecmd' -- identity card:"
        assert fields["Tools"] == "3 tool(s)" and fields["Command"] == "dz"

    def test_unsupported_level_raises(self):
        eng = _engine()
        with pytest.raises(ValueError):
            interrogate(eng, eng, level="planet")


class TestInterrogateTool:
    def test_full_view_has_identity_then_state(self, tmp_path):
        tool = _tool("widget")
        interro = interrogate(
            tool, _engine(), level="tool", project_root=str(tmp_path))
        assert interro.level == "tool"
        assert [s.name for s in interro.sections] == ["identity", "state"]
        identity, state = interro.sections
        assert identity.kind == "fields"
        assert identity.title == "Tool 'widget' -- identity card:"
        fields = dict(identity.rows)
        assert fields["Kind"] == "tool"
        assert fields["Name"] == "widget"
        assert fields["Language"] == "python"
        assert fields["Tags"] == "a, b"
        assert state.kind == "fields" and state.title == "Current state:"

    def test_state_facet_projects_mode_missing_without_a_directory(self, tmp_path):
        # A tool with no directory reads as MISSING -- the no-filesystem path.
        tool = _tool("ghost", directory=None)
        interro = interrogate(
            tool, _engine(), level="tool", facets={"state"},
            project_root=str(tmp_path))
        assert [s.name for s in interro.sections] == ["state"]
        assert dict(interro.sections[0].rows)["Mode"] == "MISSING"

    def test_embedded_tool_reads_embedded(self, tmp_path):
        d = tmp_path / "widget"
        d.mkdir()
        tool = _tool("widget", directory=str(d))
        interro = interrogate(
            tool, _engine(), level="tool", facets={"state"},
            project_root=str(tmp_path))
        assert dict(interro.sections[0].rows)["Mode"] == "EMBEDDED"

    def test_identity_facet_only_is_the_reduction(self, tmp_path):
        tool = _tool("widget")
        interro = interrogate(
            tool, _engine(), level="tool", facets={"identity"},
            project_root=str(tmp_path))
        assert [s.name for s in interro.sections] == ["identity"]

    def test_json_nests_mode_under_state(self, tmp_path, capsys):
        tool = _tool("ghost", directory=None)
        interro = interrogate(
            tool, _engine(), level="tool", project_root=str(tmp_path))
        render_interrogation(interro, as_json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["name"] == "ghost"
        assert payload["state"]["mode"] == "MISSING"


# --- render_interrogation: the one display layer ----------------------------


class TestRender:
    def test_kit_card_prints_identity_and_state(self, tmp_path, capsys):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", project_root=str(tmp_path))
        assert render_interrogation(interro) == 0
        out = capsys.readouterr().out
        assert "Kit 'demo' -- identity card:" in out
        assert "Current state:" in out
        assert "activation" in out and "(enable <-> disable)" in out

    def test_json_mirrors_the_card_with_a_state_object(self, tmp_path, capsys):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", project_root=str(tmp_path))
        assert render_interrogation(interro, as_json=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["name"] == "demo"
        assert payload["state"]["activation"] == "active"

    def test_state_facet_reduction_prints_no_identity_card(self, tmp_path, capsys):
        kit = _kit("demo")
        interro = interrogate(
            kit, _engine(kits=[kit]), level="kit", facets={"state"},
            project_root=str(tmp_path))
        render_interrogation(interro)
        out = capsys.readouterr().out
        assert "identity card" not in out
        assert "Current state:" in out

    def test_empty_state_section_prints_no_header(self, capsys):
        # A state section with no rows (e.g. a not-found kit) stays silent.
        interro = Interrogation(level="kit", sections=[
            Section(name="state", kind="axes", rows=[], title="Current state:")])
        render_interrogation(interro)
        assert "Current state:" not in capsys.readouterr().out


# --- membership / structure facets (the list/tree referent, invariant-full) -


class TestMembershipStructure:
    def test_aggregator_membership_is_kits_plus_tools(self):
        # The invariant-full referent: an aggregator's members = ALL kits AND
        # ALL tools (the whole subtree), kits first then tools.
        kits = [_kit("core", tools=("find", "grep")), _kit("media", tools=("mp3me",))]
        tools = [_tool("find"), _tool("mp3me")]
        rows = membership_rows(None, _engine(), "aggregator", projects=tools, kits=kits)
        assert [k for k, *_ in rows] == ["kit", "kit", "tool", "tool"]
        assert ("kit", "core", "2 tool(s)") in rows
        assert ("tool", "find", "") in rows

    def test_kit_membership_is_its_tools(self):
        kit = _kit("core", tools=("find", "grep"))
        assert membership_rows(kit, _engine(), "kit") == [
            ("tool", "find", ""), ("tool", "grep", "")]

    def test_tool_membership_is_empty_leaf(self):
        assert membership_rows(_tool("find"), _engine(), "tool") == []

    def test_aggregator_structure_groups_tools_by_kit(self):
        kits = [_kit("core", tools=("find", "grep")), _kit("media", tools=("mp3me",))]
        assert structure_rows(None, _engine(), "aggregator", kits=kits) == [
            ("core", ["find", "grep"]), ("media", ["mp3me"])]

    def test_membership_is_opt_in_not_in_full_info(self, tmp_path):
        # facets=None (full info) must NOT pull the member list -- info is the
        # node's OWN read; list/tree are opt-in child reads.
        kit = _kit("core", tools=("find",))
        interro = interrogate(kit, _engine(kits=[kit]), level="kit",
                              project_root=str(tmp_path))
        names = [s.name for s in interro.sections]
        assert "membership" not in names and "structure" not in names

    def test_interrogate_aggregator_membership_facet(self):
        kits = [_kit("core", tools=("find",))]
        eng = _engine()
        interro = interrogate(eng, eng, level="aggregator", facets={"membership"},
                              projects=[_tool("find")], kits=kits)
        assert [s.name for s in interro.sections] == ["membership"]
        assert interro.sections[0].kind == "list"

    def test_interrogate_kit_structure_facet(self):
        kit = _kit("core", tools=("find", "grep"))
        interro = interrogate(kit, _engine(), level="kit", facets={"structure"})
        assert [s.name for s in interro.sections] == ["structure"]
        assert interro.sections[0].rows == [("core", ["find", "grep"])]

    def test_render_list_and_tree_sections(self, capsys):
        kits = [_kit("core", tools=("find", "grep"))]
        eng = _engine()
        interro = interrogate(eng, eng, level="aggregator",
                              facets={"membership", "structure"},
                              projects=[_tool("find")], kits=kits)
        render_interrogation(interro)
        out = capsys.readouterr().out
        assert "Members:" in out and "[kit] core" in out
        assert "Structure:" in out and "- find" in out

    def test_json_members_and_structure(self, capsys):
        kits = [_kit("core", tools=("find",))]
        eng = _engine()
        interro = interrogate(eng, eng, level="aggregator",
                              facets={"membership", "structure"},
                              projects=[_tool("find")], kits=kits)
        render_interrogation(interro, as_json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["members"][0]["kind"] == "kit"
        assert payload["structure"]["core"] == ["find"]
