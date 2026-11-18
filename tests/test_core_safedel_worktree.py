"""Tearing down a git worktree without deleting through its links.

Background: on 2026-11-17 ``git worktree remove`` deleted THROUGH the
``private/`` junctions of two worktrees and emptied the real repositories
they pointed at (git 2.52.0.windows.1; with ``--force``, and without it when
``private/`` is gitignored). ``_worktree`` is the replacement: remove the
links only, check their targets, move the folder to the trash, check again,
``git worktree prune``.

Safety (rule 1b -- a test must not be ABLE to do what it asserts is
prevented):
- every repository, worktree, link and trash store is under ``tmp_path``;
  the store has an explicit registry path, so nothing under the home folder
  is read or written;
- tests asserting a REFUSAL use a store whose ``trash`` raises;
- ``git worktree remove`` is never run: one test replaces ``subprocess.run``
  in the module and fails if any git command other than ``worktree prune``
  is issued;
- links are made with ``_winapi.CreateJunction`` / ``os.symlink`` and
  inspected with ``lexists``; nothing is resolved.
"""
import json
import os
import subprocess
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _junctions, _worktree

WINDOWS = sys.platform == "win32"


def _git(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
         "-c", "user.name=t", "-c", "commit.gpgsign=false",
         "-c", "tag.gpgsign=false", *args],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _dir_link(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    os.symlink(str(target), str(link), target_is_directory=True)
    return "symlink"


def _store(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


class _NoTrash:
    """Rule 1b: a store that cannot move anything."""
    def trash(self, *a, **k):
        raise AssertionError("a refusal failed: the store was asked to move something")


@pytest.fixture
def repo(tmp_path):
    """A main repo with a nested ``private`` repo, and a worktree whose
    ``private`` is a link to it -- the layout of 2026-11-17."""
    main = tmp_path / "proj"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "README.md").write_text("x\n")
    (main / ".gitignore").write_text("private/\n")
    _git(main, "add", "README.md", ".gitignore")
    _git(main, "commit", "-q", "-m", "init")
    private = main / "private"
    (private / "claude").mkdir(parents=True)
    for i in range(3):
        (private / "claude" / f"note{i}.md").write_text(f"note {i}\n")
    _git(private, "init", "-q", "-b", "main")
    wt = tmp_path / "proj__feature"
    _git(main, "worktree", "add", "-q", "-b", "feat", str(wt), "main")
    kind = _dir_link(private, wt / "private")
    (wt / "work.txt").write_text("uncommitted work\n")
    return main, private, wt, kind


def _notes(private):
    return sorted(os.listdir(private / "claude"))


# -- planning ----------------------------------------------------------------


def test_plan_finds_the_link_and_changes_nothing(repo, tmp_path):
    main, private, wt, kind = repo

    plan = _worktree.plan_teardown(str(wt))

    assert not plan.refused
    assert plan.main_repo == str(main) and plan.worktree_name == wt.name
    assert [(l.path, l.link_type, l.target_exists) for l in plan.links] == \
        [(str(wt / "private"), kind, True)]
    assert plan.links[0].target_entries == len(os.listdir(private))
    text = "\n".join(plan.describe())
    assert "will NOT be touched" in text and "git worktree prune" in text
    assert os.path.lexists(wt / "private") and (wt / "work.txt").exists()
    assert not (tmp_path / "trash").exists()


@pytest.mark.parametrize("what", ["main-checkout", "plain-folder", "missing", "a-link"])
def test_what_is_not_a_linked_worktree_is_refused(repo, tmp_path, what):
    main, private, wt, kind = repo
    plain = tmp_path / "plain"
    plain.mkdir()
    target = {"main-checkout": main, "plain-folder": plain,
              "missing": tmp_path / "nope", "a-link": wt / "private"}[what]

    plan = _worktree.plan_teardown(str(target))
    result = _worktree.execute_teardown(plan, _NoTrash())

    assert plan.refused and result.refused and not result.success
    assert _notes(private) == ["note0.md", "note1.md", "note2.md"]
    assert wt.exists()


def test_the_main_checkout_refusal_says_why(repo):
    main, private, wt, kind = repo
    plan = _worktree.plan_teardown(str(main))
    assert "main checkout" in plan.refusals[0]


# -- the teardown ------------------------------------------------------------


def test_teardown_removes_the_worktree_and_leaves_the_target_alone(repo, tmp_path):
    main, private, wt, kind = repo
    before = _notes(private)
    store = _store(tmp_path)

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)), store)

    assert result.success, result.errors
    assert not os.path.lexists(wt)                        # the worktree is gone
    assert _notes(private) == before                      # the target is intact
    assert (private / "claude" / "note1.md").read_text() == "note 1\n"
    assert len(result.batches) == 2                       # the link, then the folder
    listed = _git(main, "worktree", "list")
    assert str(wt).replace("\\", "/") not in listed.replace("\\", "/")
    assert "git worktree prune" in result.steps[-1]
    # the uncommitted work went to the trash with the folder, recoverable
    trashed = os.path.join(str(tmp_path / "trash"), result.batches[-1],
                           "content", wt.name, "work.txt")
    assert open(trashed).read() == "uncommitted work\n"


def test_steps_happen_in_the_safe_order(repo, tmp_path):
    main, private, wt, kind = repo
    store = _store(tmp_path)
    order = []
    real = store.trash

    def spy(paths, **kw):
        order.append(os.path.basename(paths[0]))
        return real(paths, **kw)
    store.trash = spy

    result = _worktree.execute_teardown(
        _worktree.plan_teardown(str(wt)), store,
        prune=lambda repo_path: order.append("prune:" + os.path.basename(repo_path))
        or subprocess.CompletedProcess([], 0))

    assert result.success
    assert order == ["private", wt.name, "prune:" + main.name]   # link, folder, prune


def test_links_at_depth_and_with_the_same_name_are_all_removed(repo, tmp_path):
    main, private, wt, kind = repo
    other = tmp_path / "elsewhere"
    (other / "keep").mkdir(parents=True)
    (other / "keep" / "f.txt").write_text("F\n")
    _dir_link(other / "keep", wt / "sub" / "deep" / "private")   # same name, deeper

    plan = _worktree.plan_teardown(str(wt))
    assert [os.path.relpath(l.path, wt).replace("\\", "/") for l in plan.links] == \
        ["sub/deep/private", "private"]                          # deepest first
    result = _worktree.execute_teardown(plan, _store(tmp_path))

    assert result.success, result.errors
    assert not os.path.lexists(wt)
    assert (other / "keep" / "f.txt").read_text() == "F\n"
    assert _notes(private) == ["note0.md", "note1.md", "note2.md"]
    assert len(result.batches) == 3


def test_a_worktree_with_no_links_is_just_moved_and_pruned(repo, tmp_path):
    main, private, wt, kind = repo
    # remove only the link (lexists / rmdir / unlink act on the link itself)
    if WINDOWS:
        os.rmdir(wt / "private")
    else:
        os.unlink(wt / "private")

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)),
                                        _store(tmp_path))

    assert result.success and len(result.batches) == 1
    assert not os.path.lexists(wt) and _notes(private)


# -- stopping when a check fails ---------------------------------------------


def test_a_target_that_changes_stops_the_teardown_before_the_folder_moves(
        repo, tmp_path):
    main, private, wt, kind = repo
    store = _store(tmp_path)
    real = store.trash

    def damages_target(paths, **kw):
        out = real(paths, **kw)
        if os.path.basename(paths[0]) == "private":
            os.unlink(private / "README-extra") if (private / "README-extra").exists() else None
            (private / "appeared.txt").write_text("something changed the target\n")
        return out
    store.trash = damages_target

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)), store)

    assert not result.success
    assert any("entries" in e for e in result.errors)
    assert wt.exists() and (wt / "work.txt").exists()     # the folder did not move
    assert len(result.batches) == 1                       # only the link's batch


def test_a_link_that_cannot_be_removed_stops_everything(repo, tmp_path):
    main, private, wt, kind = repo

    class _Refusing:
        def trash(self, paths, **kw):
            return sd.TrashResult(success=False, refused=True, folder_name="",
                                  folder_path="", errors=["refused for the test"])

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)),
                                        _Refusing())

    assert not result.success and result.refused
    assert os.path.lexists(wt / "private") and wt.exists()
    assert _notes(private) == ["note0.md", "note1.md", "note2.md"]


def test_a_failed_prune_is_reported(repo, tmp_path):
    main, private, wt, kind = repo

    result = _worktree.execute_teardown(
        _worktree.plan_teardown(str(wt)), _store(tmp_path),
        prune=lambda repo_path: subprocess.CompletedProcess(
            [], 128, stdout="", stderr="fatal: not a git repository"))

    assert not result.success
    assert "git worktree prune failed" in result.errors[0]
    assert not os.path.lexists(wt)                        # the safe steps had finished
    assert _notes(private) == ["note0.md", "note1.md", "note2.md"]


def test_the_only_git_command_ever_issued_is_worktree_prune(repo, tmp_path,
                                                            monkeypatch):
    main, private, wt, kind = repo
    issued = []
    real_run = subprocess.run

    def watch(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and cmd and "git" in os.path.basename(str(cmd[0])).lower():
            issued.append(list(cmd))
            assert "remove" not in cmd, "git worktree remove must never be run"
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(_worktree.subprocess, "run", watch)

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)),
                                        _store(tmp_path))

    assert result.success
    assert issued == [["git", "-C", str(main), "worktree", "prune"]]


# -- the registry ------------------------------------------------------------


def test_registry_round_trip(tmp_path):
    reg = str(tmp_path / "state" / "junctions.json")
    target = tmp_path / "data"
    target.mkdir()
    link = tmp_path / "view"
    kind = _dir_link(target, link)

    _junctions.register_link(str(link), str(target), kind, reg, created_by="test")
    _junctions.register_link(str(link), str(target), kind, reg)       # replaces, not duplicates

    records = _junctions.read_registry(reg)
    assert len(records) == 1 and records[0]["type"] == kind
    assert json.load(open(reg))[0]["link"] == str(link)
    hits = _junctions.links_pointing_at(str(target), reg)
    assert [(h.link_path, h.source) for h in hits] == [(str(link), "registry")]

    assert _junctions.unregister_link(str(link), reg) is True
    assert _junctions.unregister_link(str(link), reg) is False
    assert _junctions.read_registry(reg) == []
    assert _junctions.links_pointing_at(str(target), reg) == []


def test_an_unreadable_registry_is_an_empty_one(tmp_path):
    reg = tmp_path / "junctions.json"
    reg.write_text("{ not json")
    assert _junctions.read_registry(str(reg)) == []
    assert _junctions.read_registry(str(tmp_path / "absent.json")) == []


def test_teardown_forgets_the_links_it_removed(repo, tmp_path):
    main, private, wt, kind = repo
    reg = str(tmp_path / "junctions.json")
    _junctions.register_link(str(wt / "private"), str(private), kind, reg)

    result = _worktree.execute_teardown(_worktree.plan_teardown(str(wt)),
                                        _store(tmp_path), registry_path=reg)

    assert result.success
    assert _junctions.read_registry(reg) == []
