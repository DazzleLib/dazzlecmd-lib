"""
Recovery, cleanup, listing, and status subcommands for safedel.

Implements:
    safedel list [time-pattern] [--json] [--age ">Nd"]
    safedel recover [time-pattern] [--to PATH] [--metadata-only] [--dry-run]
    safedel clean [time-pattern] [--force] [--yes] [--age ">Nd"] [-q] [-qq]
    safedel status
"""

import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._store import TrashFolder, TrashStore, TrashEntry
from ._timepattern import resolve_time_args, parse_folder_datetime
from ._zones import (
    Zone,
    check_clean_permission,
    determine_zone,
    format_age,
    get_zone_warnings,
    load_config,
)

# Metadata preservation comes from filekit (the canonical home; preservelib was
# a re-export shim).
from dazzle_filekit.metadata import apply_file_metadata


# -- List --


def _resolve_folders(
    store: TrashStore,
    positional_args: List[str],
    contains: Optional[str] = None,
    path_pattern: Optional[str] = None,
    age_filter: Optional[str] = None,
) -> List[TrashFolder]:
    """Resolve user arguments to matching TrashFolder objects across all stores."""
    from ._timepattern import time_pattern_to_glob, get_most_recent_folder

    # --contains and --path search: scan all manifests
    if contains or path_pattern:
        all_folders = store.list_entries()
        results = []
        for folder in all_folders:
            for entry in folder.entries:
                if contains and _fnmatch(entry.original_name, contains):
                    results.append(folder)
                    break
                if path_pattern:
                    orig = entry.original_path.replace("\\", "/")
                    if _fnmatch(orig, path_pattern) or _fnmatch(entry.original_path, path_pattern):
                        results.append(folder)
                        break
        return results

    # Time-pattern based matching
    glob_pattern = time_pattern_to_glob(positional_args)

    if glob_pattern is None:
        # "last" -- the most recent COMPLETE batch across all stores. A batch
        # that never finished (a run that crashed or was stopped) or whose
        # manifest cannot be read is not what "the last deletion" means, and
        # with two sessions it may be someone else's run in progress. Such a
        # batch is recovered by naming it exactly.
        all_folders = store.list_entries()
        skipped = []
        for folder in reversed(all_folders):
            if folder.state == "complete":
                for s in skipped:
                    print(f"  [skipping {s.folder_name}: {_state_note(s)}; "
                          f"name it exactly to recover it]", file=sys.stderr)
                return [folder]
            skipped.append(folder)
        for s in skipped:
            print(f"  [skipping {s.folder_name}: {_state_note(s)}; "
                  f"name it exactly to recover it]", file=sys.stderr)
        return []

    return store.list_entries(pattern=glob_pattern, age_filter=age_filter)


def _state_note(folder: TrashFolder) -> str:
    """Why a batch is not an ordinary complete one, in a few words."""
    if folder.manifest_error:
        return f"its manifest is {folder.manifest_error}"
    if folder.state == "staging":
        return "the run that made it did not finish"
    return f"state {folder.state}"


def _remove_recovered_folder(store: TrashStore, folder: TrashFolder
                             ) -> Optional[str]:
    """Remove a batch folder after recovery -- only when it is provably
    spent. Returns None when removed, or the reason it was kept.

    A folder is removed only if its manifest was readable, it listed at
    least one entry, and nothing is left under ``content/``. Anything else
    means the folder may still hold the only copy of something, so it stays.
    The folder is removed by its exact path, never by a name search across
    stores.
    """
    if folder.manifest_error:
        return f"its manifest is {folder.manifest_error}"
    if not folder.entries:
        return "its manifest lists no entries"
    left = TrashStore.leftover_content(folder)
    if left:
        return (f"{len(left)} item(s) are still in it "
                f"(first: {left[0]})")
    if not store.remove_folder(folder.folder_name, folder.folder_path):
        return "it could not be removed"
    return None


def _fnmatch(name: str, pattern: str) -> bool:
    """Filename match helper."""
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def _path_parent_accessible(path: str) -> bool:
    """Check if a path's parent directory exists and is accessible.

    Used for WSL dual-path fallback: a path is "usable" if its parent
    dir exists so we can recover into it. The path itself may not exist
    yet (we're recovering into it).
    """
    if not path:
        return False
    try:
        parent = os.path.dirname(path) or "."
        return os.path.isdir(parent)
    except (OSError, ValueError):
        return False


def cmd_list(
    store: TrashStore,
    positional_args: List[str],
    contains: Optional[str] = None,
    path_pattern: Optional[str] = None,
    age_filter: Optional[str] = None,
    json_output: bool = False,
    count: Optional[int] = None,
) -> int:
    """List trash contents matching the given pattern.

    ``count`` caps the output to the N MOST RECENT matching folders. The library
    default is ``None`` -- show all (backward-compatible for non-CLI callers).
    The ``dz safedel list`` CLI passes ``count=10`` by default so a large trash
    store doesn't flood the terminal; ``--count 0`` / ``--all`` restores "show
    all". ``count <= 0`` or ``None`` means no limit.
    """
    folders = _resolve_folders(
        store, positional_args,
        contains=contains, path_pattern=path_pattern,
        age_filter=age_filter,
    )
    # `_resolve_folders` returns folders oldest-first; the N most recent are the
    # tail. Keep that order so the newest still renders last (closest to the
    # prompt), like `tail`.
    total = len(folders)
    truncated = count is not None and count > 0 and total > count
    if truncated:
        folders = folders[-count:]
    folder_names = [f.folder_name for f in folders]

    if not folder_names:
        if not json_output:
            print("  No matching entries in trash.")
        else:
            print("[]")
        return 0

    config = load_config()
    now = datetime.datetime.now()

    # The folders are used as found. Looking each one up again BY NAME would
    # return the first store's folder when two stores hold the same name.
    if json_output:
        items = [_folder_to_json(folder, config, now) for folder in folders]
        print(json.dumps(items, indent=2, default=str))
        return 0

    # Table output. Name the store(s) that actually hold what is listed --
    # usually a per-volume store, not the central default.
    stores = sorted({os.path.dirname(f.folder_path) for f in folders})
    if len(stores) == 1:
        print(f"\n  Trash store: {stores[0]}")
    else:
        print(f"\n  Trash stores ({len(stores)}):")
        for s in stores:
            print(f"    {s}")
    if truncated:
        print(f"  {len(folder_names)} most recent of {total} folder(s) "
              f"(--count 0 or --all to show all):\n")
    else:
        print(f"  {len(folder_names)} matching folder(s):\n")

    for folder in folders:
        name = folder.folder_name

        age = now - folder.deleted_at
        zone = determine_zone(folder.deleted_at, config, now)
        age_str = format_age(age)

        print(f"  [{zone.label}] {name} ({age_str} ago)")
        for entry in folder.entries:
            type_str = entry.file_type
            target_str = ""
            if entry.link_target:
                target_str = f" -> {entry.link_target}"
            size_str = ""
            if entry.stat and entry.stat.get("st_size"):
                size_str = f"  ({_format_size(entry.stat['st_size'])})"
            print(f"    {entry.original_name}  {type_str}{target_str}{size_str}")
            print(f"    from: {entry.original_path}")
        print()

    return 0


# -- Recover --


def _recover_folder_entries(
    store: TrashStore,
    folder: TrashFolder,
    *,
    to_path: Optional[str] = None,
    metadata_only: bool = False,
    dry_run: bool = False,
    force_identity: bool = False,
) -> Tuple[int, int]:
    """Recover every entry in ``folder`` via ``_recover_entry``. Returns
    ``(recovered, errors)``.

    The shared inner work of ``cmd_recover`` (which resolves MANY folders by
    fuzzy time-pattern and accounts errors cumulatively) and ``recover_folder``
    (one exact folder). Deliberately does NOT print a summary or remove the
    folder -- those policies differ between the two callers, so each owns them.
    """
    recovered = 0
    errors = 0
    if folder.manifest_error:
        # No manifest means no record of where anything came from. That is
        # an error to report, never "0 items, all done" -- the folder's
        # content/ may hold the only copy of something.
        print(
            f"  ERROR: {folder.folder_name}: the manifest is "
            f"{folder.manifest_error}, so there is no record of where its "
            f"contents came from. Nothing was recovered and the folder was "
            f"kept: {folder.folder_path}",
            file=sys.stderr,
        )
        return 0, 1
    if folder.state == "staging":
        print(f"  NOTE: {folder.folder_name} is from a run that did not "
              f"finish; recovering what it had already moved.")
    lost = _entries_that_lost_their_content(folder)
    for entry in folder.entries:
        if id(entry) in lost:
            # A batch written before same-named items got their own places:
            # several entries point at ONE stored item, and it can only be
            # one of them. Recovering it under another entry's path would
            # put the wrong bytes there.
            verb = "Would refuse" if dry_run else "ERROR"
            print(
                f"  {verb}: {entry.original_path}: content lost to a name "
                f"collision (batch written before the fix). What is stored "
                f"at {entry.content_path} belongs to "
                f"{lost[id(entry)].original_path}.",
                file=sys.stderr,
            )
            errors += 1
            continue
        outcome = _recover_entry(
            store, folder, entry,
            to_path=to_path, metadata_only=metadata_only, dry_run=dry_run,
            force_identity=force_identity,
        )
        if outcome is SKIPPED:
            continue        # never staged: neither recovered nor a failure
        if outcome:
            recovered += 1
        else:
            errors += 1
    return recovered, errors


_LINK_ENTRY_TYPES = ("symlink_file", "symlink_dir", "junction", "broken_link")


def _content_parts(entry: TrashEntry) -> List[str]:
    """An entry's ``content_path`` as its parts. Manifests hold it with
    ``/`` (0.10.39 on) or the writer's native separator (before); both are
    accepted."""
    return [p for p in (entry.content_path or "").replace("\\", "/").split("/")
            if p]


def _content_file(folder: TrashFolder, entry: TrashEntry) -> Optional[str]:
    """Where an entry's content sits on disk, or None if it has none."""
    parts = _content_parts(entry)
    return os.path.join(folder.folder_path, *parts) if parts else None


def _batch_relpath(folder: TrashFolder, entry: TrashEntry) -> Optional[List[str]]:
    """The entry's place under ``content/`` in a common-base batch (its path
    relative to the batch's base), as parts. None for a flat batch (written
    before 0.10.39) or an entry with nothing under ``content/``."""
    layout = (folder.manifest or {}).get("content_layout")
    parts = _content_parts(entry)
    if layout != "common-base-v1" or len(parts) < 2 or parts[0] != "content":
        return None
    return parts[1:]


def _entries_that_lost_their_content(folder: TrashFolder) -> dict:
    """For a batch whose entries SHARE a stored item: ``{id(loser): owner}``.

    Before 0.10.36 two same-named items in one batch were staged onto one
    place, the later overwriting the earlier, while the manifest kept an
    entry for each. Such a manifest lists several entries with one
    ``content_path``. What is stored there is the LAST item staged whose type
    matches what is on disk (a file cannot be the survivor if a folder is
    there); every other entry of the group has lost its content. Links are
    left out: a link's entry carries its target in the manifest, not in
    ``content/``.
    """
    groups: dict = {}
    for entry in folder.entries:
        if not entry.content_path or entry.file_type in _LINK_ENTRY_TYPES:
            continue
        key = "/".join(_content_parts(entry)).casefold()
        groups.setdefault(key, []).append(entry)
    lost: dict = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        where = _content_file(folder, members[0])
        on_disk_is_dir = bool(where) and os.path.isdir(where) \
            and not os.path.islink(where)
        matching = [e for e in members if bool(e.is_dir) == on_disk_is_dir]
        owner = (matching or members)[-1]
        for entry in members:
            if entry is not owner:
                lost[id(entry)] = owner
    return lost


def _size_mismatch(entry: TrashEntry, content_path: str) -> Optional[str]:
    """Why a stored FILE is not the size the manifest recorded, or None.

    The recorded size is ``staged_size`` (0.10.39 on: measured in the trash
    when staged) or, for older batches, the original's ``st_size`` at delete
    time. Folders and links are not compared. A missing record is not a
    mismatch.
    """
    if entry.is_dir or entry.file_type in _LINK_ENTRY_TYPES:
        return None
    expected = entry.staged_size
    if expected is None and isinstance(entry.stat, dict):
        expected = entry.stat.get("st_size")
    if expected is None:
        return None
    try:
        actual = os.lstat(content_path).st_size
    except OSError:
        return None
    if int(expected) == actual:
        return None
    return (f"the manifest recorded {int(expected)} bytes and the stored "
            f"file has {actual}")


def _mtime_note(entry: TrashEntry, content_path: str) -> Optional[str]:
    """A note when a stored FILE's modification time differs from the one
    recorded at delete time, or None. Only a note: a copy across drives or
    filesystems can shift a timestamp by its resolution, and the size check
    is what decides identity. More than two seconds apart counts."""
    if entry.is_dir or entry.file_type in _LINK_ENTRY_TYPES:
        return None
    recorded = entry.stat.get("st_mtime") if isinstance(entry.stat, dict) else None
    if recorded is None:
        return None
    try:
        actual = os.lstat(content_path).st_mtime
    except OSError:
        return None
    if abs(float(recorded) - actual) <= 2.0:
        return None
    return ("the stored file's modification time differs from the one "
            "recorded when it was deleted")


# _recover_entry's third outcome: the entry was never staged (refused because
# another item of the same name was already in the batch), so there is
# nothing to recover and nothing went wrong.
SKIPPED = "skipped"


def _never_staged(entry: TrashEntry) -> bool:
    """True for an entry whose content was expected but never moved in."""
    return (
        entry.content_path is None
        and entry.content_preserved
        and entry.file_type not in ("symlink_file", "symlink_dir", "junction",
                                    "broken_link")
    )


def _skip_message(entry: TrashEntry, dry_run: bool) -> str:
    verb = "Would skip" if dry_run else "Skipped"
    if os.path.lexists(entry.original_path):
        where = "the original is still in place"
    else:
        where = "it was left in place at delete time and is no longer there"
    return f"  {verb}: {entry.original_path} -- never staged; {where}"


def cmd_recover(
    store: TrashStore,
    positional_args: List[str],
    contains: Optional[str] = None,
    path_pattern: Optional[str] = None,
    to_path: Optional[str] = None,
    metadata_only: bool = False,
    dry_run: bool = False,
    force_identity: bool = False,
) -> int:
    """Recover files from trash.

    ``force_identity`` recovers a stored file even when its size differs from
    what the manifest recorded (by default such an item is refused)."""
    folders = _resolve_folders(
        store, positional_args,
        contains=contains, path_pattern=path_pattern,
    )

    if not folders:
        print("  No matching entries to recover.", file=sys.stderr)
        return 1

    total_recovered = 0
    total_errors = 0
    kept_folders = 0

    for folder in folders:
        if not folder:
            continue

        rec, err = _recover_folder_entries(
            store, folder,
            to_path=to_path, metadata_only=metadata_only, dry_run=dry_run,
            force_identity=force_identity,
        )
        total_recovered += rec
        total_errors += err

        # If everything so far recovered cleanly and not dry-run, remove the
        # trash folder. (Cumulative `total_errors` is intentional/pre-existing:
        # a failure in an earlier folder suppresses later removals.)
        # --metadata-only never moves content out, so the folder still holds
        # the only trash copy: removing it would destroy that copy.
        if not dry_run and not metadata_only and total_errors == 0:
            kept = _remove_recovered_folder(store, folder)
            if kept:
                print(f"  Kept trash folder {folder.folder_name}: {kept}")
                kept_folders += 1

    if dry_run:
        print(f"\n  DRY RUN: Would recover {total_recovered} item(s).")
    else:
        print(f"\n  Recovered {total_recovered} item(s).")
        if total_errors:
            print(f"  {total_errors} error(s) occurred.", file=sys.stderr)

    return 1 if (total_errors > 0 or kept_folders > 0) else 0


def recover_folder(
    store: TrashStore,
    folder_name: str,
    to_path: Optional[str] = None,
    metadata_only: bool = False,
    dry_run: bool = False,
    force_identity: bool = False,
) -> int:
    """Recover one SPECIFIC trash folder by its exact name. Returns 0/1.

    Programmatic counterpart to ``cmd_recover``: where ``cmd_recover`` resolves
    user-typed positional args through fuzzy time-pattern globbing (``last``,
    ``today``, ``2026-04-0*``), this takes the exact ``folder_name`` a caller
    already holds (e.g. the ``TrashResult.folder_name`` a mode swap recorded as
    a restore origin, #37) and recovers it directly -- no glob ambiguity.

    Recovers every entry to its ``original_path`` (or under ``to_path``) via the
    shared ``_recover_folder_entries``, then removes the now-empty trash folder
    on full success. Returns 0 if all entries recovered, 1 if the folder is
    missing or any entry failed.
    """
    folder = store.get_folder(folder_name)
    if folder is None:
        print(f"  Trash folder '{folder_name}' not found.", file=sys.stderr)
        return 1

    recovered, errors = _recover_folder_entries(
        store, folder,
        to_path=to_path, metadata_only=metadata_only, dry_run=dry_run,
        force_identity=force_identity,
    )

    # --metadata-only leaves the content in the trash; keep the folder.
    kept = None
    if not dry_run and not metadata_only and errors == 0:
        kept = _remove_recovered_folder(store, folder)
        if kept:
            print(f"  Kept trash folder {folder.folder_name}: {kept}")

    if dry_run:
        print(f"\n  DRY RUN: Would recover {recovered} item(s) from "
              f"'{folder_name}'.")
    else:
        print(f"\n  Recovered {recovered} item(s) from '{folder_name}'.")
        if errors:
            print(f"  {errors} error(s) occurred.", file=sys.stderr)

    return 1 if (errors > 0 or kept) else 0


def _recover_entry(
    store: TrashStore,
    folder: TrashFolder,
    entry: TrashEntry,
    to_path: Optional[str] = None,
    metadata_only: bool = False,
    dry_run: bool = False,
    force_identity: bool = False,
) -> bool:
    """Recover a single entry from trash.

    Returns True on success, False on error.
    """
    # Determine recovery target
    if to_path:
        # --to always acts as a parent directory: recover into it. An item
        # of a common-base batch keeps its place relative to the batch's
        # base, so two same-named items do not collide under --to either;
        # an item of a flat batch (before 0.10.39) lands at <to>/<name>.
        if not dry_run:
            os.makedirs(to_path, exist_ok=True)
        relpath = _batch_relpath(folder, entry)
        if relpath:
            target = os.path.join(to_path, *relpath)
        else:
            target = os.path.join(to_path, entry.original_name)
    else:
        target = entry.original_path
        # WSL dual-path fallback: if the native original_path can't be used
        # (e.g., manifest written by WSL Python with /mnt/c/ paths, now
        # recovering from Windows Python), try the alternate form.
        if target and not _path_parent_accessible(target) and entry.original_path_alt:
            alt = entry.original_path_alt
            if _path_parent_accessible(alt):
                target = alt
                print(f"  [using alternate path form: {alt}]")

    # Metadata-only recovery
    if metadata_only:
        if dry_run:
            print(f"  Would apply metadata to: {target}")
            if entry.metadata:
                _print_metadata_summary(entry)
            return True

        if not os.path.exists(target):
            print(
                f"  ERROR: Target does not exist for metadata recovery: {target}",
                file=sys.stderr,
            )
            return False

        if entry.metadata:
            try:
                apply_file_metadata(target, entry.metadata)
                print(f"  Applied metadata to: {target}")
                return True
            except Exception as e:
                print(f"  ERROR applying metadata to {target}: {e}", file=sys.stderr)
                return False
        else:
            print(f"  No metadata stored for: {entry.original_name}", file=sys.stderr)
            return False

    # Full recovery
    content_path = _content_file(folder, entry)

    if _never_staged(entry):
        print(_skip_message(entry, dry_run))
        return SKIPPED

    # Is the stored file the one the manifest describes? A size that differs
    # means the content was changed, truncated, or belongs to another item.
    if content_path and os.path.lexists(content_path):
        mismatch = _size_mismatch(entry, content_path)
        if mismatch and not force_identity:
            verb = "Would refuse" if dry_run else "ERROR"
            print(
                f"  {verb}: {entry.original_path}: {mismatch}. Not recovered; "
                f"the stored file is still at {content_path}. To recover it "
                f"anyway pass --force-identity.",
                file=sys.stderr,
            )
            return False
        if mismatch:
            print(f"  WARNING: {entry.original_path}: {mismatch}; recovering "
                  f"anyway (--force-identity).", file=sys.stderr)
        note = _mtime_note(entry, content_path)
        if note:
            print(f"  NOTE: {entry.original_path}: {note}.", file=sys.stderr)

    if dry_run:
        print(f"  Would recover: {entry.original_name}")
        print(f"    Type: {entry.file_type}")
        print(f"    To: {target}")
        if entry.link_target:
            print(f"    Link target: {entry.link_target}")
        if content_path and os.path.exists(content_path):
            print(f"    Content: available")
        elif entry.file_type in ("symlink_file", "symlink_dir", "junction"):
            print(f"    Content: link metadata (target={entry.link_target})")
        else:
            print(f"    Content: NOT available")
        return True

    # Check for conflicts at target
    if os.path.exists(target) or os.path.islink(target):
        print(
            f"  ERROR: Target already exists: {target}\n"
            f"  Use --to to recover to a different location.",
            file=sys.stderr,
        )
        return False

    # Ensure parent directory exists
    parent = os.path.dirname(target)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    # Recover based on type
    try:
        if entry.file_type in ("symlink_file", "symlink_dir"):
            # Recreate symlink
            if entry.link_target:
                is_dir = entry.file_type == "symlink_dir"
                os.symlink(entry.link_target, target, target_is_directory=is_dir)
                print(f"  Recovered symlink: {target} -> {entry.link_target}")
                # The copy of the link kept under content/ is spent now.
                # (lexists + rmdir/unlink act on the link itself.)
                if content_path and os.path.lexists(content_path):
                    try:
                        if is_dir and sys.platform == "win32":
                            os.rmdir(content_path)
                        else:
                            os.unlink(content_path)
                    except OSError:
                        pass
            else:
                print(f"  ERROR: No link target recorded for symlink", file=sys.stderr)
                return False

        elif entry.file_type == "junction":
            # Recreate junction (Windows only)
            if sys.platform == "win32" and entry.link_target:
                _create_junction(target, entry.link_target)
                print(f"  Recovered junction: {target} -> {entry.link_target}")
            elif entry.link_target:
                print(
                    f"  WARNING: Cannot recreate junction on {sys.platform}. "
                    f"Target was: {entry.link_target}",
                    file=sys.stderr,
                )
                return False
            else:
                print(f"  ERROR: No link target recorded for junction", file=sys.stderr)
                return False

        elif content_path and os.path.exists(content_path):
            # Move content back
            if os.path.isdir(content_path):
                from dazzle_filekit.operations import copy_tree_preserving_links
                copy_tree_preserving_links(content_path, target)
                shutil.rmtree(content_path)
            else:
                shutil.copy2(content_path, target)
                os.unlink(content_path)

            # Apply preserved metadata
            if entry.metadata:
                try:
                    apply_file_metadata(target, entry.metadata)
                except Exception:
                    pass  # Best effort

            print(f"  Recovered: {target}")

        else:
            print(
                f"  ERROR: No content available to recover for: {entry.original_name}",
                file=sys.stderr,
            )
            return False

        return True

    except OSError as e:
        print(f"  ERROR recovering {entry.original_name}: {e}", file=sys.stderr)
        return False


# -- Clean --


def cmd_clean(
    store: TrashStore,
    positional_args: List[str],
    age_filter: Optional[str] = None,
    force: bool = False,
    yes: bool = False,
    verbosity: int = 0,
) -> int:
    """Permanently delete trash entries with zone-based protection."""
    folders = _resolve_folders(
        store, positional_args,
        age_filter=age_filter,
    )

    if not folders:
        print("  No matching entries to clean.")
        return 0

    config = load_config()
    now = datetime.datetime.now()
    from ._console import stdin_is_interactive
    is_tty = stdin_is_interactive()

    cleaned = 0
    skipped = 0
    failed = 0

    for folder in folders:
        name = folder.folder_name

        zone = determine_zone(folder.deleted_at, config, now)

        # Check permission
        allowed, reason = check_clean_permission(
            zone, force=force, yes=yes, is_tty=is_tty
        )
        if not allowed:
            print(f"\n  {name}: {reason}")
            skipped += 1
            continue

        # Build entry metadata for warnings
        entry_meta = {}
        if folder.entries:
            e = folder.entries[0]
            entry_meta = {
                "original_path": e.original_path,
                "file_type": e.file_type,
                "link_target": e.link_target,
                "link_count": e.link_count,
                "stat": e.stat or {},
                "age_description": format_age(now - folder.deleted_at) + " ago",
            }

        # Show warnings
        zone_warnings = get_zone_warnings(zone, entry_meta, verbosity)
        if zone_warnings:
            print()
            for w in zone_warnings:
                print(f"  {w}")

        # Show what will be deleted
        print(f"\n  Permanently delete: {name}")
        for entry in folder.entries:
            print(f"    {entry.original_name} ({entry.file_type})")
            print(f"    was at: {entry.original_path}")

        # Interactive confirmation (zones B, C, D without --yes)
        if zone.requires_interactive:
            if zone.allows_yes_override and yes:
                pass  # Zone D with --yes: skip prompt
            else:
                try:
                    answer = input("\n  Permanently delete? [y/N]: ").strip().lower()
                    if answer != "y":
                        print("  Skipped.")
                        skipped += 1
                        continue
                except (EOFError, KeyboardInterrupt):
                    print("\n  Aborted.")
                    skipped += 1
                    continue

        # Do the permanent delete -- this exact folder, not a name search
        if store.remove_folder(name, folder.folder_path):
            print(f"  Deleted: {name}")
            cleaned += 1
        else:
            print(f"  ERROR: Failed to delete {name}", file=sys.stderr)
            failed += 1

    print(f"\n  Cleaned: {cleaned}, Skipped: {skipped}"
          + (f", Failed: {failed}" if failed else ""))
    return _clean_exit_code(cleaned, skipped, failed)


def _clean_exit_code(cleaned: int, skipped: int, failed: int) -> int:
    """Graded exit code for ``clean`` (same table as the delete action).

    0 everything asked for was cleaned; 1 a delete failed and nothing else
    explains it; 3 nothing was cleaned because every entry was refused or
    declined; 4 some cleaned and some not.
    """
    from ._verdict import EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_PARTIAL
    if not skipped and not failed:
        return EXIT_OK
    if cleaned:
        return EXIT_PARTIAL
    if failed:
        return EXIT_ERROR
    return EXIT_REFUSED


# -- Status --


def cmd_status(store: TrashStore) -> int:
    """Show trash store statistics."""
    stats = store.get_stats()

    # The totals cover every store; name each one that holds something, not
    # only the central default (which is often the emptiest).
    all_folders_for_label = store.list_entries()
    per_store: Dict[str, int] = {}
    for f in all_folders_for_label:
        key = os.path.dirname(f.folder_path)
        per_store[key] = per_store.get(key, 0) + 1
    if len(per_store) <= 1:
        only = next(iter(per_store), stats.store_path)
        print(f"\n  Trash store: {only}")
    else:
        print(f"\n  Trash stores ({len(per_store)}):")
        for path in sorted(per_store):
            print(f"    {path}  ({per_store[path]} folder(s))")
    print(f"  Folders: {stats.total_folders}")
    print(f"  Entries: {stats.total_entries}")
    print(f"  Total size: {_format_size(stats.total_size_bytes)}")

    if stats.oldest:
        oldest_dt = parse_folder_datetime(stats.oldest)
        if oldest_dt:
            age = format_age(datetime.datetime.now() - oldest_dt)
            print(f"  Oldest: {stats.oldest} ({age} ago)")

    if stats.newest:
        newest_dt = parse_folder_datetime(stats.newest)
        if newest_dt:
            age = format_age(datetime.datetime.now() - newest_dt)
            print(f"  Newest: {stats.newest} ({age} ago)")

    # Zone breakdown
    if stats.total_folders > 0:
        config = load_config()
        now = datetime.datetime.now()
        zone_counts = {z: 0 for z in Zone}

        all_folders = store.list_entries()
        for folder in all_folders:
            zone = determine_zone(folder.deleted_at, config, now)
            zone_counts[zone] += 1

        print(f"\n  Zone breakdown:")
        for zone in Zone:
            count = zone_counts[zone]
            if count > 0:
                print(f"    {zone.label}: {count}")

    print()
    return 0


# -- Helpers --


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _folder_to_json(
    folder: TrashFolder, config: dict, now: datetime.datetime
) -> Dict[str, Any]:
    """Convert a TrashFolder to a JSON-serializable dict."""
    zone = determine_zone(folder.deleted_at, config, now)
    return {
        "folder_name": folder.folder_name,
        "deleted_at": folder.deleted_at.isoformat(),
        "age": format_age(now - folder.deleted_at),
        "zone": zone.name,
        "entries": [
            {
                "original_path": e.original_path,
                "original_name": e.original_name,
                "file_type": e.file_type,
                "link_target": e.link_target,
                "size": e.stat.get("st_size") if e.stat else None,
            }
            for e in folder.entries
        ],
    }


def _print_metadata_summary(entry: TrashEntry) -> None:
    """Print a summary of preserved metadata."""
    meta = entry.metadata or {}
    if "timestamps" in meta:
        ts = meta["timestamps"]
        print(f"    Timestamps:")
        print(f"      Modified: {ts.get('modified_iso', 'N/A')}")
        print(f"      Accessed: {ts.get('accessed_iso', 'N/A')}")
        print(f"      Created:  {ts.get('created_iso', 'N/A')}")
    if "mode" in meta:
        print(f"    Permissions: {oct(meta['mode'])}")


def _create_junction(link_path: str, target_path: str) -> None:
    """Re-create a Windows junction.

    Uses filekit's ``create_junction``, which quotes the paths for
    PowerShell (a ``'`` in a path used to break the command, or run part of
    the path as code) and runs with ``-NoProfile -NonInteractive`` and
    ``ErrorActionPreference=Stop``.
    """
    from dazzle_filekit.links import create_junction
    if not create_junction(target_path, link_path):
        raise OSError(
            f"Failed to create junction {link_path} -> {target_path}"
        )
