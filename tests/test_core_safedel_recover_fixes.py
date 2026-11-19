"""Recovery fixes for 0.10.37-alpha (DazzleLib/dazzlecmd-lib#7 follow-ups).

1. `recover` (and `list`) accept a batch folder name exactly as `list`
   prints it, and a single quoted "date time" argument. Before: the name got
   `__*` appended and matched nothing.
2. An entry that was never staged (refused because another item of the same
   name was already in the batch) is reported as skipped, not as
   "ERROR: No content available", and does not count as a failure.
3. `--metadata-only` never removes the trash folder: it does not move the
   content out, so removing the folder destroyed the only trash copy.

Every test uses a temporary store with an explicit registry_path, so no real
trash store is read or written.
"""
import os

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel._timepattern import time_pattern_to_glob


def _store_in(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# -- 1. folder names and quoted "date time" ---------------------------------


@pytest.mark.parametrize("args, expected", [
    (["2026-11-17__17-24-12"], "2026-11-17__17-24-12"),
    (["2026-11-17__17-24-12_001"], "2026-11-17__17-24-12_001"),
    (["2026-11-17__17-*"], "2026-11-17__17-*"),
    (["2026-11-17 17:24*"], "2026-11-17__17-24*"),
    (["2026-11-17 17:24:12"], "2026-11-17__17-24-12"),
])
def test_pattern_accepts_folder_names_and_quoted_date_time(args, expected):
    assert time_pattern_to_glob(args) == expected


@pytest.mark.parametrize("args, expected", [
    (["2026-04-08"], "2026-04-08__*"),
    (["2026-04-08", "10:4*"], "2026-04-08__10-4*"),
    (["2026-04-08", "10:46:33"], "2026-04-08__10-46-33"),
    (["2026-04-0*"], "2026-04-0*"),
    (["last"], None),
])
def test_existing_pattern_forms_are_unchanged(args, expected):
    assert time_pattern_to_glob(args) == expected


def test_recover_by_exact_folder_name(tmp_path, capsys):
    f = tmp_path / "src" / "keep.txt"
    _write(f, b"payload")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    assert result.success and not f.exists()

    rc = sd.cmd_recover(store, [result.folder_name])

    assert rc == 0, capsys.readouterr().out
    assert f.read_bytes() == b"payload"


# -- 2. never-staged entries are skipped, not errors ------------------------


def _batch_with_refused_item(tmp_path, monkeypatch):
    """A batch holding one staged item and one never-staged item -- what
    0.10.36 wrote for a same-name pair, and what any per-item staging
    failure still writes. The layout step (which from 0.10.39 gives the two
    different places) and the plan's collision check are both switched off,
    so the per-item guard produces it."""
    from dazzlecmd_lib.core.safedel import _store
    counter = iter(range(10 ** 6))
    monkeypatch.setattr(_store, "_name_key", lambda name: f"k{next(counter)}")
    monkeypatch.setattr(_store.TrashStore, "_plan_layout", lambda self, plan: None)
    a = tmp_path / "src" / "a" / "x.json"
    b = tmp_path / "src" / "b" / "x.json"
    _write(a, b"A")
    _write(b, b"B")
    store = _store_in(tmp_path)
    result = store.trash([str(a), str(b)])
    assert not result.success            # b refused by the 0.10.36 guard
    assert not a.exists() and b.exists()
    return store, result, a, b


def test_never_staged_entry_is_skipped_not_an_error(tmp_path, capsys, monkeypatch):
    store, result, a, b = _batch_with_refused_item(tmp_path, monkeypatch)

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr()
    text = out.out + out.err

    assert rc == 0, text
    assert a.read_bytes() == b"A"
    assert b.read_bytes() == b"B"                     # untouched, still in place
    assert "No content available" not in text
    assert "Skipped" in text and "never staged" in text and str(b) in text
    assert not os.path.isdir(result.folder_path)      # nothing left in the trash


def test_dry_run_says_it_would_skip_a_never_staged_entry(tmp_path, capsys, monkeypatch):
    store, result, a, b = _batch_with_refused_item(tmp_path, monkeypatch)

    sd.recover_folder(store, result.folder_name, dry_run=True)
    text = capsys.readouterr().out

    assert "Would skip" in text and "never staged" in text
    assert "Content: NOT available" not in text


# -- 3. --metadata-only never removes the trash folder ----------------------


@pytest.mark.parametrize("via", ["recover_folder", "cmd_recover"])
def test_metadata_only_keeps_the_trash_copy(tmp_path, capsys, via):
    f = tmp_path / "src" / "doc.txt"
    _write(f, b"original bytes")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    staged = os.path.join(result.folder_path, result.entries[0].content_path)
    assert os.path.isfile(staged)
    _write(f, b"a new file at the same path")         # target for metadata

    if via == "recover_folder":
        sd.recover_folder(store, result.folder_name, metadata_only=True)
    else:
        sd.cmd_recover(store, [result.folder_name], metadata_only=True)

    assert os.path.isdir(result.folder_path)
    assert open(staged, "rb").read() == b"original bytes"
