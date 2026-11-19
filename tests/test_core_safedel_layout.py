"""The common-base trash layout (0.10.39): every item gets its own place.

Before, a batch held each NAME once: ``a/x.json`` and ``b/x.json`` fought
over ``content/x.json`` (0.10.35 and earlier lost bytes; 0.10.36 refused the
second item; 0.10.37 refused the whole batch). Now each item is stored at its
path relative to the batch's common parent folder -- ``content/a/x.json`` and
``content/b/x.json`` -- so a same-name batch simply works, and recovery puts
each back where it came from.

Also here, because they ride on the layout:
- recovery under ``--to`` keeps each item's relative place;
- a stored file whose size differs from the manifest's record is refused
  unless ``force_identity`` is given;
- an OLD batch whose entries share one stored item recovers only to the
  entry that owns it, and says so for the others.

Rule 1b: everything is under ``tmp_path`` (store, registry, sources, recovery
targets). Refusal tests stub the move/delete/copy entry points so a refusal
that fails cannot move anything. The hand-built "old batch" tests write their
own manifest under ``tmp_path``; no real trash store is read.
"""
import json
import os
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _store, _verdict

WINDOWS = sys.platform == "win32"


def _store_in(tmp_path, **kw):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"), **kw)


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _manifest(result):
    with open(os.path.join(result.folder_path, "manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def _stored(result, *parts):
    return os.path.join(result.folder_path, "content", *parts)


@pytest.fixture
def no_moves(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("a refusal failed: something tried to move, "
                             "delete or copy")
    for name in ("stage_to_trash", "_stage_item", "safe_delete", "_make_copy_first"):
        monkeypatch.setattr(_store, name, _boom)


# -- staging -----------------------------------------------------------------


def test_same_named_files_each_get_their_own_place(tmp_path):
    a = _write(tmp_path / "src" / "a" / "x.json", b"AAA")
    b = _write(tmp_path / "src" / "b" / "x.json", b"BB")
    c = _write(tmp_path / "src" / "c" / "deep" / "x.json", b"C")

    result = _store_in(tmp_path).trash([str(a), str(b), str(c)])

    assert result.success, (result.errors, result.postcondition_failures)
    assert (result.staged_count, result.not_staged_count) == (3, 0)
    assert open(_stored(result, "a", "x.json"), "rb").read() == b"AAA"
    assert open(_stored(result, "b", "x.json"), "rb").read() == b"BB"
    assert open(_stored(result, "c", "deep", "x.json"), "rb").read() == b"C"
    assert not a.exists() and not b.exists() and not c.exists()
    assert _verdict.result_verdict(result)[:2] == (0, "STAGED")


def test_names_that_differ_only_by_case_also_get_their_own_place(tmp_path):
    a = _write(tmp_path / "src" / "a" / "Data.TXT", b"A")
    b = _write(tmp_path / "src" / "b" / "data.txt", b"B")

    result = _store_in(tmp_path).trash([str(a), str(b)])

    assert result.success
    assert open(_stored(result, "a", "Data.TXT"), "rb").read() == b"A"
    assert open(_stored(result, "b", "data.txt"), "rb").read() == b"B"


def test_same_named_folders_each_get_their_own_place(tmp_path):
    _write(tmp_path / "src" / "a" / "docs" / "one.md", b"1")
    _write(tmp_path / "src" / "b" / "docs" / "two.md", b"2")

    result = _store_in(tmp_path).trash([str(tmp_path / "src" / "a" / "docs"),
                                        str(tmp_path / "src" / "b" / "docs")])

    assert result.success
    assert open(_stored(result, "a", "docs", "one.md"), "rb").read() == b"1"
    assert open(_stored(result, "b", "docs", "two.md"), "rb").read() == b"2"


def test_one_item_keeps_just_its_name(tmp_path):
    f = _write(tmp_path / "src" / "deep" / "er" / "only.txt", b"O")

    result = _store_in(tmp_path).trash([str(f)])

    assert result.success
    assert os.listdir(os.path.join(result.folder_path, "content")) == ["only.txt"]
    assert _manifest(result)["entries"][0]["content_path"] == "content/only.txt"


def test_the_manifest_records_the_layout_the_base_and_file_sizes(tmp_path):
    a = _write(tmp_path / "src" / "a" / "x.json", b"AAA")
    b = _write(tmp_path / "src" / "b" / "x.json", b"BB")
    _write(tmp_path / "src" / "d" / "inner.txt", b"I")

    result = _store_in(tmp_path).trash([str(a), str(b), str(tmp_path / "src" / "d")])
    m = _manifest(result)

    assert m["content_layout"] == "common-base-v1"
    assert [b_["native"] for b_ in m["bases"]] == [str(tmp_path / "src")]
    assert m["bases"][0]["key"]                              # its portable key
    by_path = {e["original_path"]: e for e in m["entries"]}
    assert by_path[str(a)]["content_path"] == "content/a/x.json"    # "/" on every platform
    assert by_path[str(b)]["content_path"] == "content/b/x.json"
    assert (by_path[str(a)]["staged_size"], by_path[str(b)]["staged_size"]) == (3, 2)
    assert by_path[str(tmp_path / "src" / "d")]["staged_size"] is None   # folders: no size
    assert m["state"] == "complete"


def test_a_path_given_twice_is_staged_once_with_a_warning(tmp_path):
    f = _write(tmp_path / "src" / "a.txt", b"A")

    result = _store_in(tmp_path).trash([str(f), str(f)])

    assert result.success and result.staged_count == 1
    assert any("kept once" in w for w in result.warnings)


def test_an_item_inside_another_item_is_carried_by_it(tmp_path):
    folder = tmp_path / "src" / "build"
    inner = _write(folder / "out" / "a.o", b"O")
    other = _write(tmp_path / "src" / "notes.txt", b"N")

    result = _store_in(tmp_path).trash([str(folder), str(inner), str(other)])

    assert result.success and result.staged_count == 2          # the inner item is not a third
    assert any("carries it" in w for w in result.warnings)
    assert open(_stored(result, "build", "out", "a.o"), "rb").read() == b"O"
    assert [e.original_path for e in result.entries] == [str(folder), str(other)]


def test_the_preview_shows_the_same_places_the_run_uses(tmp_path):
    a = _write(tmp_path / "src" / "a" / "x.json", b"A")
    b = _write(tmp_path / "src" / "b" / "x.json", b"B")
    store = _store_in(tmp_path)

    plan = store.plan([str(a), str(b)])
    assert not plan.refused
    assert [i.staged_name for i in plan.items] == ["a/x.json", "b/x.json"]
    assert a.exists() and b.exists()                            # planning moved nothing
    assert not (tmp_path / "trash").exists()

    result = store.execute(plan)
    assert result.success
    assert sorted(e["content_path"] for e in _manifest(result)["entries"]) == [
        "content/a/x.json", "content/b/x.json"]


def test_a_drive_root_is_refused_and_nothing_moves(tmp_path, no_moves, monkeypatch):
    # The planner refuses a root as an item. A real root is never handed to
    # the store here: the layout planner is given one in place of the test
    # file's path, and the moving functions are stubbed to raise.
    f = _write(tmp_path / "src" / "a.txt", b"A")
    import dazzle_filekit.batch_layout as bl
    real = bl.plan_batch
    root = "C:\\" if WINDOWS else "/"
    monkeypatch.setattr(bl, "plan_batch",
                        lambda paths, **kw: real([root], **kw))

    result = _store_in(tmp_path).trash([str(f)])

    assert result.refused
    assert "cannot be laid out" in result.errors[0] and "root" in result.errors[0]
    assert f.read_bytes() == b"A"
    assert not (tmp_path / "trash").exists()


def test_an_item_inside_a_link_that_is_also_named_is_refused(tmp_path, no_moves):
    # "delete this link AND this thing inside it": the link would be removed
    # as a link, but the thing is in what the link points at. Refused whole.
    kept = tmp_path / "kept"
    _write(kept / "inner.txt", b"I")
    link = tmp_path / "src" / "lnk"
    link.parent.mkdir(parents=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(kept), str(link))
    else:
        os.symlink(str(kept), str(link), target_is_directory=True)

    result = _store_in(tmp_path).trash([str(link), os.path.join(str(link), "inner.txt")])

    assert result.refused
    assert any("which is a link" in e for e in result.errors)
    assert os.path.lexists(link) and (kept / "inner.txt").read_bytes() == b"I"


def test_a_missing_planner_is_a_refusal_not_a_crash(tmp_path, no_moves, monkeypatch):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    monkeypatch.setitem(sys.modules, "dazzle_filekit.batch_layout", None)

    result = _store_in(tmp_path).trash([str(f)])

    assert result.refused
    assert "dazzle-filekit 0.5.0 or later" in result.errors[0]
    assert f.read_bytes() == b"A"


def test_items_under_two_roots_get_numbered_prefixes_and_a_warning(tmp_path, monkeypatch):
    # Two drives cannot be built in a temp folder, so the planner is asked
    # to treat the two source folders as two roots: it is handed stand-in
    # paths on two "drives" and its answer is mapped back onto the real
    # files. What is under test is the store's use of a multi-root answer.
    a = _write(tmp_path / "one" / "docs" / "f.txt", b"A")
    b = _write(tmp_path / "two" / "docs" / "f.txt", b"B")
    import dazzle_filekit.batch_layout as bl
    real = bl.plan_batch
    stand_in = {str(a): r"C:\one\docs\f.txt", str(b): r"E:\two\docs\f.txt"}

    def two_roots(paths, **kw):
        kw = dict(kw, flavor="windows", target_flavor="windows")
        kw.pop("is_link", None)
        plan = real([stand_in[p] for p in paths], **kw)
        back = {v: k for k, v in stand_in.items()}
        for item in plan.items:
            item.original = back[item.original]
        return plan
    monkeypatch.setattr(bl, "plan_batch", two_roots)

    result = _store_in(tmp_path).trash([str(a), str(b)])

    assert result.success, (result.errors, result.postcondition_failures)
    assert open(_stored(result, "0", "f.txt"), "rb").read() == b"A"
    assert open(_stored(result, "1", "f.txt"), "rb").read() == b"B"
    assert any("spans 2 drives or roots" in w for w in result.warnings)
    assert len(_manifest(result)["bases"]) == 2


# -- recovery ----------------------------------------------------------------


def test_a_same_name_batch_recovers_each_item_to_where_it_came_from(tmp_path, capsys):
    a = _write(tmp_path / "src" / "a" / "x.json", b"AAA")
    b = _write(tmp_path / "src" / "b" / "x.json", b"BB")
    store = _store_in(tmp_path)
    result = store.trash([str(a), str(b)])
    assert result.success and not a.exists() and not b.exists()

    rc = sd.recover_folder(store, result.folder_name)

    assert rc == 0, capsys.readouterr()
    assert (a.read_bytes(), b.read_bytes()) == (b"AAA", b"BB")
    assert not os.path.isdir(result.folder_path)        # spent, so removed (empty folders and all)


def test_recovery_to_another_folder_keeps_each_items_place(tmp_path, capsys):
    a = _write(tmp_path / "src" / "a" / "x.json", b"AAA")
    b = _write(tmp_path / "src" / "b" / "x.json", b"BB")
    store = _store_in(tmp_path)
    result = store.trash([str(a), str(b)])
    out = tmp_path / "restored"

    rc = sd.recover_folder(store, result.folder_name, to_path=str(out))

    assert rc == 0, capsys.readouterr()
    assert (out / "a" / "x.json").read_bytes() == b"AAA"
    assert (out / "b" / "x.json").read_bytes() == b"BB"
    assert not a.exists() and not b.exists()             # not put back at the originals


def test_recovering_one_item_to_another_folder_uses_its_name(tmp_path):
    f = _write(tmp_path / "src" / "deep" / "only.txt", b"O")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    assert sd.recover_folder(store, result.folder_name,
                             to_path=str(tmp_path / "restored")) == 0
    assert (tmp_path / "restored" / "only.txt").read_bytes() == b"O"


def test_a_dry_run_of_recovery_to_another_folder_creates_nothing(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    out = tmp_path / "restored"

    sd.recover_folder(store, result.folder_name, to_path=str(out), dry_run=True)

    assert not out.exists()
    assert os.path.isfile(_stored(result, "a.txt"))


# -- the size check ----------------------------------------------------------


def _tampered_batch(tmp_path):
    f = _write(tmp_path / "src" / "a.txt", b"original")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    with open(_stored(result, "a.txt"), "wb") as fh:    # changed while in the trash
        fh.write(b"shorter")
    return store, result, f


def test_a_stored_file_of_the_wrong_size_is_not_recovered(tmp_path, capsys):
    store, result, f = _tampered_batch(tmp_path)

    rc = sd.recover_folder(store, result.folder_name)
    text = capsys.readouterr().err

    assert rc == 1
    assert not f.exists()                                # nothing put back
    assert os.path.isfile(_stored(result, "a.txt"))      # and nothing lost: still in the trash
    assert "recorded 8 bytes" in text and "has 7" in text and "--force-identity" in text


def test_force_identity_recovers_it_and_says_so(tmp_path, capsys):
    store, result, f = _tampered_batch(tmp_path)

    rc = sd.recover_folder(store, result.folder_name, force_identity=True)

    assert rc == 0
    assert f.read_bytes() == b"shorter"
    assert "recovering anyway" in capsys.readouterr().err


def test_an_older_batch_is_checked_against_the_originals_size(tmp_path, capsys):
    store, result, f = _tampered_batch(tmp_path)
    path = os.path.join(result.folder_path, "manifest.json")
    m = _manifest(result)
    del m["entries"][0]["staged_size"]                   # as written before 0.10.39
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh)

    assert sd.recover_folder(store, result.folder_name) == 1
    assert "recorded 8 bytes" in capsys.readouterr().err
    assert not f.exists()


def test_a_changed_modification_time_is_noted_but_does_not_stop_recovery(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"same size")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    stored = _stored(result, "a.txt")
    os.utime(stored, (1_000_000_000, 1_000_000_000))    # touched while in the trash

    rc = sd.recover_folder(store, result.folder_name)

    assert rc == 0 and f.read_bytes() == b"same size"   # size matches: recovered
    assert "modification time differs" in capsys.readouterr().err


def test_an_untouched_file_gets_no_note(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    assert sd.recover_folder(store, result.folder_name) == 0
    assert "modification time" not in capsys.readouterr().err


# -- old batches whose entries share one stored item -------------------------


def _old_shared_batch(tmp_path, entries, stored_name, stored):
    """Hand-build a flat batch as 0.10.35 wrote one: several entries, ONE
    stored item. ``entries`` is a list of (original path, is_dir, size);
    ``stored`` is bytes for a file or a dict of {name: bytes} for a folder."""
    folder = tmp_path / "trash" / "2026-11-09__14-03-11"
    content = folder / "content"
    if isinstance(stored, dict):
        for name, data in stored.items():
            _write(content / stored_name / name, data)
    else:
        _write(content / stored_name, stored)
    manifest = {
        "version": 1, "folder_name": folder.name,
        "entries": [{
            "original_path": str(path), "original_name": os.path.basename(str(path)),
            "file_type": "regular_dir" if is_dir else "regular_file",
            "is_dir": is_dir, "content_preserved": True,
            "content_path": os.path.join("content", stored_name),    # native separator, as then
            "stat": {"st_size": size},
        } for path, is_dir, size in entries],
    }
    with open(folder / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return folder


def test_an_old_file_on_file_collision_recovers_only_the_last_entry(tmp_path, capsys):
    a = tmp_path / "src" / "a" / "x.json"
    b = tmp_path / "src" / "b" / "x.json"
    c = tmp_path / "src" / "c" / "x.json"
    folder = _old_shared_batch(tmp_path, [(a, False, 5), (b, False, 9), (c, False, 4)],
                               "x.json", b"CCCC")        # the last one staged survived
    store = _store_in(tmp_path)

    rc = sd.recover_folder(store, folder.name)
    text = capsys.readouterr().err

    assert rc == 1                                       # two entries could not be recovered
    assert c.read_bytes() == b"CCCC"                     # the survivor, at ITS path
    assert not a.exists() and not b.exists()             # never the wrong bytes under another path
    assert text.count("content lost to a name collision") == 2
    assert str(a) in text and str(b) in text and str(c) in text
    assert folder.is_dir()                               # kept: its manifest is the only record


def test_an_old_folder_then_file_collision_gives_the_content_to_the_matching_type(
        tmp_path, capsys):
    d = tmp_path / "src" / "a" / "thing"
    f = tmp_path / "src" / "b" / "thing"
    # a folder was staged first; the later FILE could not replace a folder,
    # so what is on disk is the folder -- it belongs to the folder's entry.
    folder = _old_shared_batch(tmp_path, [(d, True, 0), (f, False, 3)],
                               "thing", {"inner.txt": b"I"})
    store = _store_in(tmp_path)

    rc = sd.recover_folder(store, folder.name)

    assert rc == 1
    assert (d / "inner.txt").read_bytes() == b"I"
    assert not f.exists()
    assert "content lost to a name collision" in capsys.readouterr().err


def test_a_dry_run_of_an_old_shared_batch_changes_nothing(tmp_path, capsys):
    a = tmp_path / "src" / "a" / "x.json"
    b = tmp_path / "src" / "b" / "x.json"
    folder = _old_shared_batch(tmp_path, [(a, False, 1), (b, False, 2)], "x.json", b"BB")
    store = _store_in(tmp_path)

    sd.recover_folder(store, folder.name, dry_run=True)

    assert "Would refuse" in capsys.readouterr().err
    assert (folder / "content" / "x.json").read_bytes() == b"BB"
    assert not a.exists() and not b.exists()


def test_an_old_flat_batch_with_distinct_names_still_recovers(tmp_path, capsys):
    a = tmp_path / "src" / "a.txt"
    folder = tmp_path / "trash" / "2026-10-01__10-00-00"
    _write(folder / "content" / "a.txt", b"A")
    with open(folder / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "folder_name": folder.name, "entries": [{
            "original_path": str(a), "original_name": "a.txt",
            "file_type": "regular_file", "is_dir": False,
            "content_preserved": True,
            "content_path": os.path.join("content", "a.txt"),
            "stat": {"st_size": 1}}]}, fh)
    store = _store_in(tmp_path)

    assert sd.recover_folder(store, folder.name,
                             to_path=str(tmp_path / "restored")) == 0
    assert (tmp_path / "restored" / "a.txt").read_bytes() == b"A"   # flat batch: <to>/<name>
    assert not folder.exists()
