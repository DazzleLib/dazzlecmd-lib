"""Every file under dazzlecmd_lib/templates/ must be shipped in the wheel.

setuptools expands `[tool.setuptools.package-data]` patterns with recursive
glob, and `*` / `**` skip dot-named files AND directories. A template under a
dot-folder (the ci component's `.github/workflows/`) was silently left out of
the published wheel, so `dz new --with ci` reported "template dir missing".
This test applies the pyproject patterns the same way and fails if any
template file would be left out.
"""
import glob
import os

import pytest

tomllib = pytest.importorskip("tomllib")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "dazzlecmd_lib")


def _patterns():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["setuptools"]["package-data"]["dazzlecmd_lib"]


def _matched_files(patterns):
    matched = set()
    for pattern in patterns:
        for path in glob.glob(os.path.join(PKG, pattern), recursive=True):
            if os.path.isfile(path):
                matched.add(os.path.normcase(os.path.relpath(path, PKG)))
    return matched


def test_every_template_file_is_packaged():
    templates = os.path.join(PKG, "templates")
    on_disk = set()
    for dirpath, _dirs, files in os.walk(templates):
        if "__pycache__" in dirpath:
            continue
        for name in files:
            on_disk.add(os.path.normcase(os.path.relpath(os.path.join(dirpath, name), PKG)))

    missing = sorted(on_disk - _matched_files(_patterns()))
    assert not missing, f"template files the wheel would leave out: {missing}"


def test_ci_component_templates_are_packaged():
    """The concrete regression: the ci component's workflow templates."""
    matched = _matched_files(_patterns())
    for name in ("test.yml.tmpl", "release.yml.tmpl"):
        rel = os.path.normcase(os.path.join(
            "templates", "__with__", "ci", ".github", "workflows", name))
        assert rel in matched, rel
