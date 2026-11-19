"""safedel tells the truth (0.10.37-alpha): plan/execute, refusals, copy-first,
post-conditions, console detection, scoped --yes, graded verdicts, and a
recovery that cannot delete what it did not restore.

Background: on 2026-11-17 ``dz safedel`` prompted with nobody at the keyboard,
printed "Aborted." and exited 0; the command line that called it then deleted
through two junctions. These tests pin the behaviour that replaces each link
of that chain.

Safety (global rule 1b -- a test must not be ABLE to do what it asserts is
prevented):
- every store is ``TrashStore(store_path=<tmp>, registry_path=<tmp>)``: no
  per-volume routing, nothing under the home folder is read or written;
- every test that asserts a REFUSAL first replaces the functions that move
  or delete (``stage_to_trash``, ``safe_delete``, ``copy_first``) with ones
  that raise, so a refusal that fails cannot move anything -- it fails the
  test instead;
- tests asserting "the folder was NOT removed" replace ``remove_folder`` or
  ``shutil.rmtree`` with a function that raises;
- nothing here resolves a path: links are made and inspected with ``lstat``,
  ``readlink`` and ``lexists`` only.
"""
import json
import os
import shutil
import sys
import types

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import (
    _console, _copyfirst, _junctions, _recover, _scopes, _store, _timepattern,
    _verdict, _volumes,
)

WINDOWS = sys.platform == "win32"


# -- helpers -----------------------------------------------------------------


def _store_in(tmp_path, **kw):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"), **kw)


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _dir_link(target, link):
    """Make a directory link without a shell and without resolving anything.
    Windows: a junction. Elsewhere: a symlink. Returns the kind."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    os.symlink(str(target), str(link), target_is_directory=True)
    return "symlink"


def _through_scope(kind):
    return ("delete-through-junction" if kind == "junction"
            else "delete-through-symlink")


def _other_scope(kind):
    return ("delete-through-symlink" if kind == "junction"
            else "delete-through-junction")


@pytest.fixture
def no_moves(monkeypatch):
    """Rule 1b: make moving, deleting and copying impossible for the test."""
    def _boom(*a, **k):
        raise AssertionError("a refusal failed: something tried to move, "
                             "delete or copy")
    monkeypatch.setattr(_store, "stage_to_trash", _boom)
    # From 0.10.39 an item with a place in the batch is staged through
    # _stage_item; blocking only the older call would leave the real move
    # reachable, and these refusal tests would be safe only while the code
    # under test is correct.
    monkeypatch.setattr(_store, "_stage_item", _boom)
    monkeypatch.setattr(_store, "safe_delete", _boom)
    monkeypatch.setattr(_store, "_make_copy_first", _boom)


def _manifest(result):
    with open(os.path.join(result.folder_path, "manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


# -- console detection -------------------------------------------------------


class _Stream:
    def __init__(self, tty=False, boom=False):
        self._tty, self._boom = tty, boom

    def isatty(self):
        if self._boom:
            raise ValueError("closed")
        return self._tty


@pytest.mark.parametrize("handle, console, pipe, expected", [
    (11, True, None, True),                       # a real console
    (11, False, None, False),                     # NUL: a char device, no console
    (11, False, r"\msys-1888ae32e00d56aa-pty0-from-master", True),   # mintty
    (11, False, r"\cygwin-1888ae32e00d56aa-pty12-from-master", True),
    (11, False, r"\1888ae32e00d56aa-87160-pipe-nt-0x1", False),      # a plain pipe
    (None, True, None, False),                    # no OS handle at all
    (-1, True, None, False),                      # invalid handle
])
def test_windows_console_decision_table(handle, console, pipe, expected):
    got = _console.stdin_is_interactive(
        _Stream(tty=True),                         # isatty() lies on Windows: ignored
        platform="win32",
        os_handle=lambda s: handle,
        is_console=lambda h: console,
        pipe_name=lambda h: pipe,
    )
    assert got is expected


def test_nul_stdin_is_not_interactive_even_though_isatty_says_yes():
    """The 2026-11-17 condition: isatty() True, GetConsoleMode fails."""
    stream = _Stream(tty=True)
    assert stream.isatty() is True
    assert _console.stdin_is_interactive(
        stream, platform="win32", os_handle=lambda s: 3,
        is_console=lambda h: False, pipe_name=lambda h: None) is False


@pytest.mark.parametrize("stream, expected", [
    (_Stream(tty=True), True),
    (_Stream(tty=False), False),
    (_Stream(boom=True), False),
])
def test_posix_uses_isatty_and_fails_toward_not_interactive(stream, expected):
    assert _console.stdin_is_interactive(stream, platform="linux") is expected


# -- scoped --yes ------------------------------------------------------------


@pytest.mark.parametrize("argv, rest, scopes, given", [
    (["a.txt"], ["a.txt"], (), False),
    (["--yes", "a.txt"], ["a.txt"], ("noninteractive",), True),
    (["a.txt", "--yes"], ["a.txt"], ("noninteractive",), True),
    (["-y", "a.txt"], ["a.txt"], ("noninteractive",), True),
    (["--yes", "dangerous", "x"], ["x"], ("dangerous",), True),
    (["--yes", "Copy-First", "x"], ["x"], ("copy-first",), True),
    (["--yes", "noninteractive,delete-through-junction", "x"], ["x"],
     ("noninteractive", "delete-through-junction"), True),
    (["--yes=delete-through-symlink", "x"], ["x"],
     ("delete-through-symlink",), True),
    (["--yes", "-n", "x"], ["-n", "x"], ("noninteractive",), True),
    (["--yes", "my,file.txt"], ["my,file.txt"], ("noninteractive",), True),
    (["--", "--yes", "x"], ["--", "--yes", "x"], (), False),
    (["--yes", "--", "dangerous"], ["--", "dangerous"], ("noninteractive",), True),
])
def test_yes_never_swallows_a_path(argv, rest, scopes, given):
    assert _scopes.split_yes(argv) == (rest, scopes, given)


@pytest.mark.parametrize("argv", [
    ["--yes", "dangerous,typo", "x"],        # a mistyped scope is not a path
    ["--yes=bogus", "x"],
    ["--yes=", "x"],
    ["--yes", "copy-first,no-copy-first", "x"],
    ["--yes", "copy-first", "--yes", "no-copy-first", "x"],
])
def test_bad_scope_lists_are_errors_not_consent(argv):
    with pytest.raises(_scopes.ScopeError):
        _scopes.split_yes(argv)


def test_parse_scopes_dedupes_and_rejects_unknown():
    assert _scopes.parse_scopes("") == ("noninteractive",)
    assert _scopes.parse_scopes("dangerous,dangerous") == ("dangerous",)
    with pytest.raises(_scopes.ScopeError):
        _scopes.parse_scopes("delete-thru-junction")
    # the contradictory pair is refused by the parser itself, not only by
    # split_yes's own check across several --yes (added 2026-11-18: the
    # red-green matrix showed the parser's check had no test of its own)
    with pytest.raises(_scopes.ScopeError):
        _scopes.parse_scopes("copy-first,no-copy-first")


# -- verdicts and exit codes -------------------------------------------------


def _res(**kw):
    base = dict(refused=False, staged_count=0, not_staged_count=0,
                postcondition_failures=[], errors=[])
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("result, code, outcome", [
    (_res(staged_count=2), 0, "STAGED"),
    (_res(refused=True, errors=["--yes was not given"]), 3, "REFUSED"),
    (_res(staged_count=1, not_staged_count=1, errors=["x"]), 4, "PARTIAL"),
    (_res(not_staged_count=2, errors=["rename failed"]), 1, "ERROR"),
    (_res(staged_count=1, postcondition_failures=["still exists"]), 6,
     "POSTCONDITION-FAILED"),
    # a failed check outranks "partial": something is not as reported
    (_res(staged_count=1, not_staged_count=1,
          postcondition_failures=["still exists"]), 6, "POSTCONDITION-FAILED"),
    # a refusal outranks everything: nothing moved
    (_res(refused=True, staged_count=5, errors=["why"]), 3, "REFUSED"),
    # errors with clean-looking counts are never reported as STAGED
    (_res(staged_count=2, errors=["something went wrong"]), 4, "PARTIAL"),
])
def test_result_verdict_table(result, code, outcome):
    got_code, got_outcome, _detail = _verdict.result_verdict(result)
    assert (got_code, got_outcome) == (code, outcome)


def test_verdict_line_is_one_line():
    line = _verdict.verdict_line("safedel", "REFUSED", "first\nsecond   third")
    assert line == "safedel: REFUSED first second third"
    assert _verdict.verdict_line("safedel", "STAGED", "") == "safedel: STAGED"


def test_exit_code_values_are_the_published_ones():
    assert (_verdict.EXIT_OK, _verdict.EXIT_ERROR, _verdict.EXIT_USAGE,
            _verdict.EXIT_REFUSED, _verdict.EXIT_PARTIAL, _verdict.EXIT_BLOCKED,
            _verdict.EXIT_POSTCONDITION) == (0, 1, 2, 3, 4, 5, 6)


# -- plan, then execute ------------------------------------------------------


def test_plan_changes_nothing(tmp_path):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)

    plan = store.plan([str(f)])

    assert not plan.refused and len(plan.items) == 1
    assert f.read_bytes() == b"A"
    assert not (tmp_path / "trash").exists()        # not even the store folder
    assert not (tmp_path / "reg.json").exists()


def test_dry_run_is_the_plan_and_moves_nothing(tmp_path, no_moves):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(f)], dry_run=True)

    assert result.success and not result.refused
    assert result.plan is not None and result.plan.items[0].path == str(f)
    assert result.folder_name == "" and f.exists()
    assert not (tmp_path / "trash").exists()


def test_preview_equals_real(tmp_path):
    """What the plan says will move is exactly what moved, and where."""
    src = tmp_path / "src"
    _write(src / "file.txt", b"F")
    _write(src / "tree" / "one.txt", b"1")
    _write(src / "tree" / "sub" / "two.txt", b"22")
    _write(src / "tree" / ".hidden", b"h")
    kept = _write(tmp_path / "kept" / "precious.txt", b"P").parent
    _dir_link(kept, src / "tree" / "inner-link")     # a link INSIDE an item
    _dir_link(kept, src / "top-link")                # a link AS an item
    _write(src / "stays.txt", b"S")                  # not in the batch
    store = _store_in(tmp_path)
    paths = [str(src / "file.txt"), str(src / "tree"), str(src / "top-link")]

    plan = store.plan(paths)
    said_will_move = {i.path for i in plan.items}
    said_names = {i.staged_name for i in plan.items if i.staged_name}
    assert not plan.refused
    assert said_will_move == set(paths)

    result = store.execute(plan)

    assert result.success, (result.errors, result.postcondition_failures)
    gone = {p for p in paths if not os.path.lexists(p)}
    assert gone == said_will_move                    # preview == real
    content = os.path.join(result.folder_path, "content")
    assert set(os.listdir(content)) == said_names    # and where it said
    assert result.staged_count == len(plan.items)
    assert (src / "stays.txt").read_bytes() == b"S"
    assert (kept / "precious.txt").read_bytes() == b"P"   # links never followed
    # the link inside the tree travelled as a link, not as a copy of its target
    inner = os.path.join(content, "tree", "inner-link")
    assert os.path.lexists(inner)
    assert not os.path.isfile(os.path.join(inner, "precious.txt")) or \
        os.stat(os.path.join(inner, "precious.txt")).st_ino == \
        os.stat(kept / "precious.txt").st_ino         # same file seen through the link


def test_describe_names_kinds_and_actions(tmp_path):
    src = tmp_path / "src"
    _write(src / "tree" / "one.txt", b"1")
    kept = _write(tmp_path / "kept" / "p.txt", b"P").parent
    kind = _dir_link(kept, src / "lnk")
    store = _store_in(tmp_path)

    text = "\n".join(store.plan([str(src / "tree"), str(src / "lnk"),
                                 str(src / "nope")]).describe())

    assert "1 file(s), 0 folder(s)" in text
    assert "remove the link only" in text
    assert ("junction" if kind == "junction" else "symlink") in text
    assert "does not exist" in text
    assert str(tmp_path / "trash") in text


def test_manifest_is_written_first_and_marked_complete(tmp_path):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(f)], scopes=("noninteractive",))

    m = _manifest(result)
    assert m["state"] == "complete"
    assert m["scopes"] == ["noninteractive"]
    assert m["entries"][0]["content_path"]


def test_interrupted_run_leaves_a_manifest_for_what_already_moved(
        tmp_path, monkeypatch, capsys):
    a = _write(tmp_path / "src" / "a.txt", b"A")
    b = _write(tmp_path / "src" / "b.txt", b"B")
    store = _store_in(tmp_path)
    real = _store._stage_item
    calls = []

    def stage_then_die(path, dest, c):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return real(path, dest, c)
    monkeypatch.setattr(_store, "_stage_item", stage_then_die)

    with pytest.raises(KeyboardInterrupt):
        store.trash([str(a), str(b)])
    monkeypatch.setattr(_store, "_stage_item", real)

    folders = store.list_entries()
    assert len(folders) == 1 and folders[0].state == "staging"
    assert [e.original_path for e in folders[0].entries] == [str(a)]
    assert not a.exists() and b.read_bytes() == b"B"

    # "last" does not pick up a run that never finished ...
    assert sd.cmd_recover(store, ["last"]) == 1
    assert not a.exists()
    # ... but naming the batch recovers what it had moved.
    assert sd.cmd_recover(store, [folders[0].folder_name]) == 0
    assert a.read_bytes() == b"A"


def test_a_run_interrupted_during_its_first_item_still_leaves_a_manifest(
        tmp_path, monkeypatch):
    """The manifest exists BEFORE the first item moves.

    Added 2026-11-18: the red-green matrix showed nothing failed when the
    first write was removed. The test above interrupts the SECOND item, by
    which time a manifest has been written after the first. If the first
    write were missing, a run that died on its first item would leave a
    batch folder with no manifest -- which recovery treats as an error it
    cannot explain.
    """
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)

    def die(path, dest, c):
        raise KeyboardInterrupt()
    monkeypatch.setattr(_store, "_stage_item", die)

    with pytest.raises(KeyboardInterrupt):
        store.trash([str(f)])

    assert f.read_bytes() == b"A"                       # nothing moved
    folders = store.list_entries()
    assert len(folders) == 1
    assert folders[0].manifest_error is None            # a readable manifest ...
    assert folders[0].state == "staging"                # ... that says the run did not finish


# -- two items on one place in the batch are refused whole -------------------
# From 0.10.39 same-NAMED items get different places (see
# test_core_safedel_layout.py), so this collision has to be manufactured by
# switching the layout step off. The check it exercises is the plan's own
# last look before anything moves.


@pytest.mark.parametrize("names", [("x.json", "x.json"), ("Data.TXT", "data.txt")])
def test_two_items_on_one_place_are_refused_whole(tmp_path, no_moves, names, monkeypatch):
    monkeypatch.setattr(_store.TrashStore, "_plan_layout", lambda self, plan: None)
    a = _write(tmp_path / "src" / "a" / names[0], b"A")
    b = _write(tmp_path / "src" / "b" / names[1], b"B")
    c = _write(tmp_path / "src" / "other.txt", b"C")

    result = _store_in(tmp_path).trash([str(a), str(b), str(c)])

    assert result.refused and not result.success
    assert "stored at one place" in result.errors[0]
    assert (a.read_bytes(), b.read_bytes(), c.read_bytes()) == (b"A", b"B", b"C")
    assert not (tmp_path / "trash").exists()        # no batch folder at all
    assert _verdict.result_verdict(result)[0] == 3


def test_all_paths_missing_is_a_refusal(tmp_path, no_moves):
    result = _store_in(tmp_path).trash([str(tmp_path / "nope")])
    assert result.refused
    assert _verdict.result_verdict(result)[:2] == (3, "REFUSED")


def test_one_missing_path_makes_the_result_partial(tmp_path):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(f), str(tmp_path / "typo.txt")])

    assert not result.success and not f.exists()
    assert (result.staged_count, result.not_staged_count) == (1, 1)
    assert _verdict.result_verdict(result)[:2] == (4, "PARTIAL")


# -- copy-first --------------------------------------------------------------


def test_copy_first_keeps_a_verified_copy_and_still_moves_the_original(tmp_path):
    f = _write(tmp_path / "src" / "doc.txt", b"payload" * 100)

    result = _store_in(tmp_path).trash([str(f)], copy_first=True)

    assert result.success and not f.exists()
    moved = os.path.join(result.folder_path, "content", "doc.txt")
    copy = os.path.join(result.folder_path, "copy-first", "doc.txt")
    assert open(moved, "rb").read() == open(copy, "rb").read() == b"payload" * 100
    rec = _manifest(result)["entries"][0]["copy_first"]
    assert rec["verified"] is True and rec["algorithm"] == "SHA256"
    assert rec["files"] == 1 and rec["bytes"] == 700
    assert rec["path"] == "copy-first/doc.txt"


def test_copy_first_of_a_tree_never_enters_a_link(tmp_path):
    src = tmp_path / "src" / "tree"
    _write(src / "one.txt", b"1")
    _write(src / "sub" / "two.txt", b"22")
    outside = _write(tmp_path / "outside" / "precious.txt", b"P").parent
    _dir_link(outside, src / "private")

    result = _store_in(tmp_path).trash([str(src)], copy_first=True)

    assert result.success, result.errors
    copy = os.path.join(result.folder_path, "copy-first", "tree")
    assert open(os.path.join(copy, "one.txt"), "rb").read() == b"1"
    assert open(os.path.join(copy, "sub", "two.txt"), "rb").read() == b"22"
    assert not os.path.lexists(os.path.join(copy, "private"))   # not copied through
    rec = _manifest(result)["entries"][0]["copy_first"]
    assert rec["files"] == 2 and rec["bytes"] == 3
    assert [l["path"] for l in rec["links_not_copied"]] == ["private"]
    assert (outside / "precious.txt").read_bytes() == b"P"


def test_copy_first_over_its_limit_refuses_the_batch(tmp_path, no_moves):
    f = _write(tmp_path / "src" / "big.bin", b"z" * 200)

    result = _store_in(tmp_path).trash([str(f)], copy_first=True,
                                       copy_first_max=100)

    assert result.refused and f.read_bytes() == b"z" * 200
    assert "no-copy-first" in result.errors[0]
    assert "--copy-first-max" in result.errors[0]


def test_copy_first_at_its_limit_is_allowed(tmp_path):
    f = _write(tmp_path / "src" / "ok.bin", b"z" * 100)
    result = _store_in(tmp_path).trash([str(f)], copy_first=True,
                                       copy_first_max=100)
    assert result.success


def test_a_copy_that_does_not_verify_leaves_the_original_in_place(
        tmp_path, monkeypatch):
    f = _write(tmp_path / "src" / "doc.txt", b"payload")
    digests = iter(["aaa", "bbb"])
    monkeypatch.setattr(_copyfirst, "_sha256", lambda p: next(digests))

    result = _store_in(tmp_path).trash([str(f)], copy_first=True)

    assert not result.success and f.read_bytes() == b"payload"
    assert "copy-first failed" in result.errors[0]
    assert (result.staged_count, result.not_staged_count) == (0, 1)
    assert not os.path.lexists(
        os.path.join(result.folder_path, "copy-first", "doc.txt"))
    assert _verdict.result_verdict(result)[0] == 1


def test_a_link_item_gets_no_copy(tmp_path):
    kept = _write(tmp_path / "kept" / "p.txt", b"P").parent
    link = tmp_path / "src" / "lnk"
    _dir_link(kept, link)

    result = _store_in(tmp_path).trash([str(link)], copy_first=True)

    assert result.success and not os.path.lexists(link)
    assert not os.path.lexists(os.path.join(result.folder_path, "copy-first"))
    assert (kept / "p.txt").read_bytes() == b"P"


def test_parse_size():
    assert _copyfirst.parse_size("10MB") == 10 * 1024 * 1024
    assert _copyfirst.parse_size("512k") == 512 * 1024
    assert _copyfirst.parse_size("1g") == 1024 ** 3
    assert _copyfirst.parse_size(4096) == 4096
    with pytest.raises(ValueError):
        _copyfirst.parse_size("lots")


def test_survey_counts_without_following_links(tmp_path):
    src = tmp_path / "tree"
    _write(src / "a.txt", b"12345")
    _write(src / "sub" / "b.txt", b"12")
    outside = _write(tmp_path / "outside" / "huge.bin", b"z" * 5000).parent
    _dir_link(outside, src / "lnk")

    s = _copyfirst.survey(str(src))

    assert (s["files"], s["dirs"], s["links"], s["bytes"]) == (2, 1, 1, 7)


# -- post-conditions ---------------------------------------------------------


def test_an_original_that_is_still_there_fails_the_check(tmp_path, monkeypatch):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    def claims_success_but_moves_nothing(path, dest_path, c):
        return _store.StagingResult(success=True, source_path=path,
                                    dest_path=dest_path, method="rename")
    monkeypatch.setattr(_store, "_stage_item", claims_success_but_moves_nothing)

    result = _store_in(tmp_path).trash([str(f)])

    assert not result.success
    assert any("still exists" in p for p in result.postcondition_failures)
    assert any("is not at" in p for p in result.postcondition_failures)
    assert _verdict.result_verdict(result)[:2] == (6, "POSTCONDITION-FAILED")


def test_a_link_target_that_changed_fails_the_check(tmp_path, monkeypatch):
    kept = tmp_path / "kept"
    _write(kept / "one.txt", b"1")
    _write(kept / "two.txt", b"2")
    link = tmp_path / "src" / "lnk"
    _dir_link(kept, link)
    real = _store.safe_delete

    def unlink_and_damage_target(path, c):
        out = real(path, c)
        os.unlink(kept / "two.txt")      # what a delete-through would do
        return out
    monkeypatch.setattr(_store, "safe_delete", unlink_and_damage_target)

    result = _store_in(tmp_path).trash([str(link)])

    assert not result.success
    assert any("had 2 entries before and has 1 now" in p
               for p in result.postcondition_failures)
    assert _verdict.result_verdict(result)[0] == 6


def test_a_clean_run_has_no_postcondition_failures(tmp_path):
    kept = _write(tmp_path / "kept" / "one.txt", b"1").parent
    link = tmp_path / "src" / "lnk"
    _dir_link(kept, link)
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(link), str(f)])

    assert result.success and result.postcondition_failures == []
    assert _verdict.result_verdict(result)[:2] == (0, "STAGED")


# -- the folder a known link points at ---------------------------------------


def _incident_shape(tmp_path, relative_gitdir=False):
    """A repo with a nested ``private`` repo, and a worktree whose ``private``
    is a link to it -- the layout of 2026-11-17."""
    proj = tmp_path / "proj"
    private = proj / "private"
    _write(private / "notes.md", b"notes")
    (private / ".git").mkdir()                       # private/ is its own repo
    wt = tmp_path / "proj__wt"
    wt.mkdir()
    entry = proj / ".git" / "worktrees" / "wt1"
    entry.mkdir(parents=True)
    gitdir = (os.path.relpath(wt / ".git", entry).replace("\\", "/")
              if relative_gitdir else str(wt / ".git").replace("\\", "/"))
    (entry / "gitdir").write_text(gitdir + "\n", encoding="utf-8")
    kind = _dir_link(private, wt / "private")
    return proj, private, wt, kind


@pytest.mark.parametrize("relative", [False, True])
def test_the_destination_of_a_worktree_link_is_refused(tmp_path, no_moves, relative):
    proj, private, wt, kind = _incident_shape(tmp_path, relative)

    result = _store_in(tmp_path).trash([str(private)])

    assert result.refused
    assert str(wt / "private") in result.errors[0]
    assert _through_scope(kind) in result.errors[0]
    assert (private / "notes.md").read_bytes() == b"notes"


def test_links_pointing_at_reads_every_enclosing_repo(tmp_path):
    proj, private, wt, kind = _incident_shape(tmp_path)

    hits = _junctions.links_pointing_at(str(private))

    assert [(h.link_path, h.link_type, h.source) for h in hits] == \
        [(str(wt / "private"), kind, "worktree")]
    assert _junctions.links_pointing_at(str(proj)) == []
    assert _junctions.links_pointing_at(str(private / "notes.md")) == []


def test_something_inside_a_link_destination_is_ordinary_work(tmp_path):
    proj, private, wt, kind = _incident_shape(tmp_path)
    sub = _write(private / "drafts" / "d.md", b"d").parent

    result = _store_in(tmp_path).trash([str(sub)])

    assert result.success and not sub.exists()
    assert (private / "notes.md").exists()


def test_the_link_itself_goes_and_its_destination_stays(tmp_path):
    proj, private, wt, kind = _incident_shape(tmp_path)

    result = _store_in(tmp_path).trash([str(wt / "private")])

    assert result.success, result.errors
    assert not os.path.lexists(wt / "private")
    assert (private / "notes.md").read_bytes() == b"notes"


def test_the_matching_scope_allows_the_destination(tmp_path):
    proj, private, wt, kind = _incident_shape(tmp_path)

    result = _store_in(tmp_path).trash([str(private)],
                                       scopes=(_through_scope(kind),))

    assert result.success, result.errors
    assert not private.exists()
    moved = os.path.join(result.folder_path, "content", "private", "notes.md")
    assert open(moved, "rb").read() == b"notes"


def test_the_other_scope_does_not_allow_it(tmp_path, no_moves):
    proj, private, wt, kind = _incident_shape(tmp_path)

    result = _store_in(tmp_path).trash([str(private)],
                                       scopes=(_other_scope(kind),))

    assert result.refused and (private / "notes.md").exists()


def test_registry_links_count_while_they_still_point_there(tmp_path, no_moves):
    target = _write(tmp_path / "data" / "f.txt", b"F").parent
    link = tmp_path / "elsewhere" / "view"
    kind = _dir_link(target, link)
    registry = tmp_path / "junctions.json"
    registry.write_text(json.dumps(
        [{"link": str(link), "target": str(target)},
         {"link": str(tmp_path / "gone"), "target": str(target)}]),   # stale
        encoding="utf-8")

    hits = _junctions.links_pointing_at(str(target), str(registry))
    assert [(h.link_path, h.source) for h in hits] == [(str(link), "registry")]

    store = _store_in(tmp_path, junction_registry_path=str(registry))
    assert store.trash([str(target)]).refused
    assert (target / "f.txt").exists()


def test_isolated_stores_do_not_read_a_registry_under_home(tmp_path):
    assert _store_in(tmp_path).junction_registry_path is None


# -- delete-through ----------------------------------------------------------


def test_delete_through_stages_the_target_and_removes_the_link(tmp_path):
    kept = _write(tmp_path / "data" / "private" / "f.txt", b"F").parent
    link = tmp_path / "wt" / "private"
    kind = _dir_link(kept, link)

    result = _store_in(tmp_path).trash([str(link)],
                                       scopes=(_through_scope(kind),))

    assert result.success, (result.errors, result.postcondition_failures)
    assert not os.path.lexists(link) and not kept.exists()
    moved = os.path.join(result.folder_path, "content", "private", "f.txt")
    assert open(moved, "rb").read() == b"F"
    kinds = [e["file_type"] for e in _manifest(result)["entries"]]
    assert kinds[0] == "regular_dir" and kinds[1] in ("junction", "symlink_dir")


def test_without_the_scope_a_link_loses_only_the_link(tmp_path):
    kept = _write(tmp_path / "data" / "private" / "f.txt", b"F").parent
    link = tmp_path / "wt" / "private"
    _dir_link(kept, link)

    result = _store_in(tmp_path).trash([str(link)])

    assert result.success and not os.path.lexists(link)
    assert (kept / "f.txt").read_bytes() == b"F"


def test_a_scope_for_the_wrong_link_type_is_refused(tmp_path, no_moves):
    kept = _write(tmp_path / "data" / "f.txt", b"F").parent
    link = tmp_path / "wt" / "lnk"
    kind = _dir_link(kept, link)

    result = _store_in(tmp_path).trash([str(link)], scopes=(_other_scope(kind),))

    assert result.refused
    assert _through_scope(kind) in result.errors[0]
    assert os.path.lexists(link) and (kept / "f.txt").exists()


def test_a_delete_through_scope_with_no_link_is_refused(tmp_path, no_moves):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(f)],
                                       scopes=("delete-through-junction",))

    assert result.refused and f.exists()
    assert "no path is a junction" in result.errors[0]


# -- store paths -------------------------------------------------------------


def test_a_relative_store_root_is_not_usable():
    assert _volumes.is_usable_store_root("") is False
    assert _volumes.is_usable_store_root("relative/store") is False
    assert _volumes.is_usable_store_root(os.path.abspath("store")) is True


@pytest.mark.skipif(WINDOWS, reason="POSIX: a drive-letter path is relative")
def test_posix_never_builds_a_store_on_a_drive_letter_name(tmp_path, monkeypatch):
    """The stray ``./C:/.safedel-trash-<uid>`` folders (2026-11-09, and one
    dated 2026-04-10) came from exactly this call."""
    monkeypatch.chdir(tmp_path)
    for mount in ("C:/", "C:", "C:\\"):
        v = _volumes.VolumeInfo(device_id=1, mount_point=mount)
        assert _volumes.get_per_volume_trash_path(v) is None
        assert _volumes.is_usable_store_root(mount) is False
    assert os.listdir(tmp_path) == []                # nothing was created


@pytest.mark.skipif(not WINDOWS, reason="Windows drive handling")
def test_windows_never_builds_a_store_on_a_name_without_a_drive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Rule 1b: without the guard this call would create \.safedel-trash at
    # the root of the current drive. Make creating any folder impossible, so
    # a missing guard fails the test instead of writing to the real disk.
    def _boom(*a, **k):
        raise AssertionError("tried to create a store folder")
    monkeypatch.setattr(os, "makedirs", _boom)
    monkeypatch.setattr(os, "mkdir", _boom)
    v = _volumes.VolumeInfo(device_id=1, mount_point="not-a-drive")
    assert _volumes.get_per_volume_trash_path(v, username="nobody") is None
    assert os.listdir(tmp_path) == []


def test_a_store_on_a_relative_path_refuses_the_batch(tmp_path, no_moves, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = sd.TrashStore(store_path="relative-trash",
                          registry_path=str(tmp_path / "reg.json"))

    result = store.trash([str(f)])

    assert result.refused and "not an absolute path" in result.errors[0]
    assert not (tmp_path / "relative-trash").exists()
    with pytest.raises(ValueError):
        store.ensure_store_exists()


def test_batch_folders_are_claimed_not_just_looked_up(tmp_path):
    import datetime
    when = datetime.datetime(2026, 11, 18, 12, 0, 0)
    first = _timepattern.claim_unique_folder(str(tmp_path), when)
    second = _timepattern.claim_unique_folder(str(tmp_path), when)
    assert first != second and second.startswith(first)
    assert (tmp_path / first).is_dir() and (tmp_path / second).is_dir()


# -- recovery cannot delete what it did not restore --------------------------


@pytest.fixture
def no_removal(monkeypatch):
    """Rule 1b: the batch folder cannot be removed during the test."""
    def _boom(*a, **k):
        raise AssertionError("recovery tried to remove a batch folder it had "
                             "not restored")
    monkeypatch.setattr(sd.TrashStore, "remove_folder", _boom)
    monkeypatch.setattr(shutil, "rmtree", _boom)


def _staged_batch(tmp_path):
    f = _write(tmp_path / "src" / "only-copy.txt", b"irreplaceable")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    assert result.success
    return store, result, f


@pytest.mark.parametrize("damage", ["missing", "corrupt", "not-an-object"])
@pytest.mark.parametrize("via", ["recover_folder", "cmd_recover"])
def test_a_batch_without_a_readable_manifest_is_kept(tmp_path, capsys,
                                                     no_removal, damage, via):
    store, result, f = _staged_batch(tmp_path)
    manifest = os.path.join(result.folder_path, "manifest.json")
    if damage == "missing":
        os.unlink(manifest)
    elif damage == "corrupt":
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
    else:
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("[]")

    if via == "recover_folder":
        rc = sd.recover_folder(store, result.folder_name)
    else:
        rc = sd.cmd_recover(store, [result.folder_name])
    text = "".join(capsys.readouterr())

    assert rc == 1, text                          # not "Recovered 0 item(s)", exit 0
    assert "manifest" in text and result.folder_path in text
    kept = os.path.join(result.folder_path, "content", "only-copy.txt")
    assert open(kept, "rb").read() == b"irreplaceable"


def test_last_skips_a_batch_whose_manifest_is_unreadable(tmp_path, capsys, no_removal):
    store, result, f = _staged_batch(tmp_path)
    os.unlink(os.path.join(result.folder_path, "manifest.json"))

    rc = sd.cmd_recover(store, ["last"])
    text = capsys.readouterr().err

    assert rc == 1
    assert os.path.isdir(os.path.join(result.folder_path, "content"))
    # "last" did not pick the batch at all (added 2026-11-18: without this
    # line the test passed whether "last" skipped the batch or picked it and
    # then failed on its manifest -- exit 1 and content kept either way)
    assert "skipping" in text and "name it exactly" in text
    assert "No matching entries" in text


def test_a_folder_that_still_holds_content_is_kept(tmp_path, capsys, monkeypatch):
    """A recovery that reports success but leaves something behind must not
    remove the folder (it would take the leftover with it)."""
    store, result, f = _staged_batch(tmp_path)
    _write(tmp_path / "trash" / result.folder_name / "content" / "stray.bin", b"S")
    removed = []
    monkeypatch.setattr(sd.TrashStore, "remove_folder",
                        lambda self, *a, **k: removed.append(a) or True)

    rc = sd.recover_folder(store, result.folder_name)
    text = "".join(capsys.readouterr())

    assert f.read_bytes() == b"irreplaceable"     # the listed item came back
    assert removed == []                           # the folder was not removed
    assert rc == 1 and "Kept trash folder" in text and "stray.bin" in text


def test_recovery_removes_by_exact_path_not_by_name(tmp_path, monkeypatch):
    store, result, f = _staged_batch(tmp_path)
    seen = []
    real = sd.TrashStore.remove_folder
    monkeypatch.setattr(sd.TrashStore, "remove_folder",
                        lambda self, name, path=None: seen.append((name, path))
                        or real(self, name, path))

    assert sd.recover_folder(store, result.folder_name) == 0

    assert seen == [(result.folder_name, result.folder_path)]
    assert not os.path.isdir(result.folder_path)


def test_remove_folder_by_path_touches_only_that_folder(tmp_path):
    """Two stores can hold batches with the same name; removing one by its
    path must leave the other alone."""
    name = "2026-11-18__12-00-00"
    a = _write(tmp_path / "storeA" / name / "content" / "a.txt", b"A").parents[1]
    b = _write(tmp_path / "storeB" / name / "content" / "b.txt", b"B").parents[1]
    store = sd.TrashStore(store_path=str(tmp_path / "storeA"),
                          registry_path=str(tmp_path / "reg.json"))

    assert store.remove_folder(name, str(b)) is True

    assert not b.exists()
    assert (a / "content" / "a.txt").read_bytes() == b"A"
    # a path whose last part is not the name is refused outright
    assert store.remove_folder("other-name", str(a)) is False
    assert a.exists()


def _two_stores(tmp_path):
    """Store A (the central one) and store B (known through the registry),
    each holding one batch."""
    reg = tmp_path / "reg.json"
    a = sd.TrashStore(store_path=str(tmp_path / "storeA"), registry_path=str(reg))
    b = sd.TrashStore(store_path=str(tmp_path / "storeB"), registry_path=str(reg))
    ra = a.trash([str(_write(tmp_path / "src" / "in-a.txt", b"A"))])
    rb = b.trash([str(_write(tmp_path / "src" / "in-b.txt", b"B"))])
    assert ra.success and rb.success
    reg.write_text(json.dumps({"serial-b": {
        "trash_path": str(tmp_path / "storeB"), "mount_point": "",
        "last_seen": "", "is_reachable": True}}), encoding="utf-8")
    return a, str(tmp_path / "storeA"), str(tmp_path / "storeB")


def test_list_and_status_name_every_store_that_holds_something(tmp_path, capsys):
    a, path_a, path_b = _two_stores(tmp_path)

    assert sd.cmd_list(a, []) == 0
    listed = capsys.readouterr().out
    assert sd.cmd_status(a) == 0
    status = capsys.readouterr().out

    for text in (listed, status):
        assert "Trash stores (2)" in text
        assert path_a in text and path_b in text
    assert "in-a.txt" in listed and "in-b.txt" in listed


def test_one_store_is_named_as_the_store_that_holds_the_batch(tmp_path, capsys):
    store, result, f = _staged_batch(tmp_path)

    sd.cmd_list(store, [])
    sd.cmd_status(store)
    out = capsys.readouterr().out

    assert out.count(f"Trash store: {os.path.dirname(result.folder_path)}") == 2


def test_clean_exit_codes():
    assert _recover._clean_exit_code(cleaned=2, skipped=0, failed=0) == 0
    assert _recover._clean_exit_code(cleaned=0, skipped=2, failed=0) == 3
    assert _recover._clean_exit_code(cleaned=1, skipped=1, failed=0) == 4
    assert _recover._clean_exit_code(cleaned=0, skipped=0, failed=1) == 1
    assert _recover._clean_exit_code(cleaned=1, skipped=0, failed=1) == 4


# -- a refusal is never forced past ------------------------------------------


def test_mode_swap_does_not_force_past_a_refusal(tmp_path, monkeypatch, capsys):
    """``--force`` overrides a FAILED backup. It must not turn safedel's
    refusal into an outright delete of the folder the refusal protected."""
    from dazzlecmd_lib import mode
    tool_dir = _write(tmp_path / "tools" / "t" / "f.py", b"code").parent

    class _RefusingStore:
        def trash(self, paths, **kw):
            return sd.TrashResult(success=False, refused=True, folder_name="",
                                  folder_path="",
                                  errors=["a junction points at this folder"])
    monkeypatch.setattr(sd, "TrashStore", _RefusingStore)
    monkeypatch.setattr(mode, "_rmtree_or_error",
                        lambda p: (_ for _ in ()).throw(
                            AssertionError("forced past a refusal")))

    rc, folder = mode._remove_tool_dir(str(tool_dir), tool_name="t",
                                       command="dz", force=True)

    assert (rc, folder) == (1, None)
    assert (tool_dir / "f.py").read_bytes() == b"code"
    assert "does not override a refusal" in capsys.readouterr().err
