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
    render_interrogation,
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
