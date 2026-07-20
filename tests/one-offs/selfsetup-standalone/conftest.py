"""Standalone harness: load self_setup.py without dazzlecmd_lib.__init__.

This machine's dazzle_lib predates VerbContext (unpushed on another dev
box), so importing the dazzlecmd_lib package's engine chain explodes.
self_setup depends only on dazzlecmd_lib.paths, whose sole heavy edge is
the core.links re-export -- stub that, load the REAL paths.py, then the
REAL self_setup.py under the package name so the test file's imports
resolve.
"""

import importlib.util
import sys
import types

LIB = r"C:\code\dazzlecmd-lib\dazzlecmd_lib"

pkg = types.ModuleType("dazzlecmd_lib")
pkg.__path__ = []
sys.modules.setdefault("dazzlecmd_lib", pkg)

# Stub the core.links re-export chain paths.py drags in at module bottom.
core = types.ModuleType("dazzlecmd_lib.core")
core.__path__ = []
links = types.ModuleType("dazzlecmd_lib.core.links")
for name in ("create_link", "get_link_target", "is_linked_project",
             "remove_link"):
    setattr(links, name, lambda *a, **k: None)
sys.modules["dazzlecmd_lib.core"] = core
sys.modules["dazzlecmd_lib.core.links"] = links
core.links = links
pkg.core = core


def _load(modname, filename):
    spec = importlib.util.spec_from_file_location(
        f"dazzlecmd_lib.{modname}", rf"{LIB}\{filename}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"dazzlecmd_lib.{modname}"] = mod
    spec.loader.exec_module(mod)
    setattr(pkg, modname, mod)
    return mod


_load("paths", "paths.py")
_load("verb_contracts", "verb_contracts.py")
_load("self_setup", "self_setup.py")
