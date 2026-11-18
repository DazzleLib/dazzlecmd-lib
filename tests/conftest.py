"""Pytest fixtures for the dazzlecmd-lib test suite."""

import os
import warnings

import pytest

_LEAK_IGNORE = {"__pycache__", ".pytest_cache"}


def _top_level_names(folder):
    try:
        return {n for n in os.listdir(folder) if n not in _LEAK_IGNORE}
    except OSError:
        return set()


@pytest.fixture(scope="session", autouse=True)
def _no_test_writes_outside_its_temp_folder():
    """Fail the run if a test left anything new beside the source.

    A test that builds a path from a Windows-style drive name on Linux, or
    forgets to give a trash store its own temp folder, writes into whatever
    directory the suite was started from (that is how folders literally named
    ``C:`` holding ``.safedel-trash-1000`` appeared in the dazzlecmd checkout
    on 2026-11-09). This watches where a stray would land: the current
    directory, the repository root and the tests folder. New top-level names
    there after the session are a failure.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    watched = {os.path.abspath(os.getcwd()), os.path.dirname(here), here}
    before = {d: _top_level_names(d) for d in watched}
    yield
    leaks = {d: sorted(_top_level_names(d) - before[d]) for d in watched}
    leaks = {d: names for d, names in leaks.items() if names}
    assert not leaks, (
        "a test wrote outside its temp folder: "
        + "; ".join(f"{d}: {names!r}" for d, names in leaks.items())
    )


@pytest.fixture
def assert_no_shim_access():
    """Return a helper asserting a callable triggers no typed-field shim access.

    The Phase 1 migration ratchet (test-time, D2-safe -- see the Phase 1 DWP):
    flips ``DazzleEntity._warn_on_shim`` ON for the duration, runs the callable,
    and asserts no shim ``DeprecationWarning`` fired -- i.e. the operation
    reached every entity's TYPED fields via attribute access. Extra /
    nested-block dict access (``entity["runtime"]``, ``entity.get("tools")``)
    does NOT warn (no safe attribute form) and is allowed.
    """
    from dazzlecmd_lib.entity import DazzleEntity

    def _run(fn, *args, **kwargs):
        prev = DazzleEntity._warn_on_shim
        DazzleEntity._warn_on_shim = True
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = fn(*args, **kwargs)
            shim = [
                w for w in caught
                if issubclass(w.category, DeprecationWarning)
                and "DazzleEntity" in str(w.message)
            ]
            assert not shim, (
                "legacy typed-field shim access detected -- migrate to attribute "
                "access:\n"
                + "\n".join(f"  {w.filename}:{w.lineno}: {w.message}" for w in shim)
            )
            return result
        finally:
            DazzleEntity._warn_on_shim = prev

    return _run


@pytest.fixture(autouse=True)
def _strip_git_hook_env(monkeypatch):
    """Strip repo-location GIT_* vars so the suite is immune to git hooks.

    git exports GIT_DIR (and friends) to hook subprocesses; a suite run from
    a pre-push hook would otherwise have every sandboxed git call silently
    address the hook's repository. Mirrors the same fixture in the parent
    repo's tests/conftest.py; production code uses
    ``dazzlecmd_lib.mode.sanitized_git_env`` for its own calls.
    """
    from dazzlecmd_lib.mode import _GIT_REPO_LOCATION_VARS
    for var in _GIT_REPO_LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _unsign_test_git_commits(monkeypatch):
    """No git commit made during a test run may GPG-sign (no pinentry spam).

    Mirrors the same fixture in the parent repo's tests/conftest.py --
    project convention is that git-using tests run unsigned against
    non-real repos. Enforced suite-wide via git's environment-config
    mechanism (git >= 2.31).
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "tag.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "false")
