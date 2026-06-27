"""SD-E (slice 6): ``default_meta_commands`` re-exports the render surface that
moved to ``rendering.py``.

Pins the public-name surface (AC-E2) so a future move can't silently break the
importers that reach into ``default_meta_commands`` -- dazzlecmd's ``parsers.py``
(``MIN_DESC_WIDTH``/``TERM_SIZE_FALLBACK``) and ``cli.py`` (the ``render_*``).
"""
import dazzlecmd_lib.default_meta_commands as dmc
import dazzlecmd_lib.rendering as rendering


def test_render_functions_reexported():
    for name in ("render_list", "render_info", "render_kit_list", "render_tree",
                 "render_version", "render_setup_listing", "build_list_entries"):
        assert hasattr(dmc, name), f"{name} not importable from default_meta_commands"


def test_layout_constants_reexported():
    # dazzlecmd's parsers.py imports these two from default_meta_commands.
    assert dmc.MIN_DESC_WIDTH == 20
    assert dmc.TERM_SIZE_FALLBACK == (80, 24)
    for name in ("KIT_NAME_COL", "SUMMARY_INDENT", "_term_width",
                 "_wrap_description", "_print_legend_entry", "_constitutional_entry"):
        assert hasattr(dmc, name), f"{name} not re-exported"


def test_render_bodies_actually_live_in_rendering():
    # The bodies moved; dmc re-exports the SAME objects from rendering (identity,
    # not a copy) -- proves the relocation, not a duplicate.
    assert dmc.render_info is rendering.render_info
    assert dmc.render_list is rendering.render_list
    assert dmc.render_tree is rendering.render_tree
    assert dmc.render_kit_list is rendering.render_kit_list


def test_handlers_and_registry_stay_in_dmc():
    # The thin layer dmc keeps: parser factories + handlers + registry.
    for name in ("list_handler", "info_handler", "tree_handler", "setup_handler",
                 "register_all", "register_selected"):
        assert name in dmc.__dict__, f"{name} should be defined in default_meta_commands"
