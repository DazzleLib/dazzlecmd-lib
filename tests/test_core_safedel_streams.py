"""NTFS alternate data streams: safedel's detector delegates to filekit's.

``core.safedel._platform.detect_alternate_streams`` was a line-for-line copy
of ``dazzle_filekit.platform.windows.detect_alternate_streams``; since 0.10.46
it calls it. These tests pin what safedel relies on (it warns before a
cross-drive copy would lose a stream): a real stream is reported, the two
ignored names are not, and any failure gives an empty list rather than an
exception during a delete.

Files are created under ``tmp_path`` only.
"""
import sys

import pytest

from dazzlecmd_lib.core.safedel import _platform

WINDOWS = sys.platform == "win32"


@pytest.mark.skipif(not WINDOWS, reason="alternate data streams are NTFS-only")
def test_a_named_stream_is_reported_and_the_download_marker_is_not(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("main")
    try:
        with open(str(f) + ":notes", "w") as fh:
            fh.write("hidden")
        with open(str(f) + ":Zone.Identifier", "w") as fh:
            fh.write("[ZoneTransfer]\nZoneId=3\n")
    except OSError:
        pytest.skip("this volume does not support alternate data streams")

    streams = _platform.detect_alternate_streams(str(f))

    assert streams == [":notes:$DATA"]
    assert _platform.has_significant_ads(str(f))


@pytest.mark.skipif(not WINDOWS, reason="alternate data streams are NTFS-only")
def test_a_plain_file_has_none(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("main")
    assert _platform.detect_alternate_streams(str(f)) == []
    assert not _platform.has_significant_ads(str(f))


@pytest.mark.skipif(not WINDOWS, reason="the delegation exists only on Windows")
def test_a_failing_filekit_call_gives_an_empty_list(tmp_path, monkeypatch):
    import dazzle_filekit.platform.windows as fkw

    def broken(path):
        raise RuntimeError("for the test")

    monkeypatch.setattr(fkw, "detect_alternate_streams", broken)
    f = tmp_path / "a.txt"
    f.write_text("main")
    assert _platform.detect_alternate_streams(str(f)) == []


def test_off_windows_the_answer_is_empty_without_importing_filekit_windows(monkeypatch):
    monkeypatch.setattr(_platform.sys, "platform", "linux")
    assert _platform.detect_alternate_streams("/anything") == []
