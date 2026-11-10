"""Make chosen modules see another os.name without touching the global one.

Tests that exercise Windows (or POSIX) branches used to do
``monkeypatch.setattr(module.os, "name", "nt")``. But ``module.os`` IS the
global ``os`` module, so that rewrote ``os.name`` for the whole process:
``pathlib.Path()`` then builds ``WindowsPath`` on Linux (or ``PosixPath`` on
Windows), which raises -- and on a failing test it crashed pytest's own
failure reporting. Swapping each module's ``os`` reference for a stand-in
keeps the change local to the modules under test.
"""
import os


class OsAs:
    """Stand-in for a module's ``os``: another ``name``, everything else real."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, attr):
        return getattr(os, attr)


def pretend_os_name(monkeypatch, name, *modules):
    """Make each of ``modules`` see ``os.name == name`` for this test."""
    for module in modules:
        monkeypatch.setattr(module, "os", OsAs(name))
