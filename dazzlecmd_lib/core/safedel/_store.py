"""
Trash store management for safedel.

Manages the timestamped trash folder structure and manifest creation.
Each delete operation creates a folder named YYYY-MM-DD__hh-mm-ss
containing a manifest.json and a content/ directory.

Layout (``content_layout: "common-base-v1"``, from 0.10.39): every item
sits under content/ at its path relative to the batch's common parent
folder, so two items with one name never share a place. The manifest's
``bases`` records that folder per drive or root. A manifest without
``content_layout`` is a flat batch from before: every item directly
under content/.

The manifest is safedel's own JSON (see dazzlecmd's MANIFEST_SCHEMA.md),
written atomically through dazzle_filekit; metadata comes from
dazzle_filekit.metadata; the layout from dazzle_filekit.batch_layout.
"""

import datetime
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._classifier import Classification, FileType, classify, format_classification
from ._platform import (
    PlatformInfo,
    StagingResult,
    DeleteResult,
    detect_platform,
    get_trash_dir,
    stage_to_trash,
    _stage_item,
    safe_delete,
    check_disk_space,
    calculate_size,
    is_same_device,
    detect_alternate_streams,
)
from ._timepattern import (
    generate_unique_folder_name,
    claim_unique_folder,
    parse_folder_datetime,
    match_trash_folders,
)
from ._volumes import (
    resolve_trash_store,
    commit_trash_store,
    get_all_trash_paths,
    is_usable_store_root,
    is_same_volume,
    _nearest_existing,
)
from ._copyfirst import (
    DEFAULT_MAX_BYTES,
    copy_first as _make_copy_first,
    _discard as _discard_copy,
    format_bytes,
    survey,
)
from ._junctions import links_pointing_at
from ._scopes import DELETE_THROUGH_JUNCTION, DELETE_THROUGH_SYMLINK

_LINK_TYPES = (
    FileType.SYMLINK_FILE, FileType.SYMLINK_DIR,
    FileType.JUNCTION, FileType.BROKEN_LINK,
)
_SYMLINK_TYPES = (FileType.SYMLINK_FILE, FileType.SYMLINK_DIR)

# How items are arranged under a batch's content/ folder. Written to every
# manifest from 0.10.39; a manifest without the field is a flat batch.
CONTENT_LAYOUT = "common-base-v1"


def _name_key(name: str) -> str:
    """Comparison key for "would these two names collide in one folder?".

    Unicode-normalised and case-folded on EVERY platform: NTFS, DrvFs and
    default macOS volumes treat ``Foo`` and ``foo`` (and composed and
    decomposed forms of one character) as the same name. Refusing a batch
    that a case-sensitive filesystem could have held costs one more call;
    letting one through that the filesystem then merges costs a file.
    """
    import unicodedata
    return unicodedata.normalize("NFC", name).casefold()

# Metadata preservation (ACLs/ADS/timestamps/xattrs) comes from filekit, the
# canonical home of that code (preservelib was a re-export shim to it).
from dazzle_filekit.metadata import collect_file_metadata, metadata_to_json


def _compute_alt_path(path: str) -> Optional[str]:
    """Compute the alternate-platform form of a path for cross-runtime recovery.

    On Windows: C:\\Users\\foo -> /mnt/c/Users/foo (WSL form)
    On Linux/WSL: /mnt/c/Users/foo -> C:\\Users\\foo (Windows form)

    Uses filekit's normalize_cross_platform_path which handles the conversion.
    Returns None if no sensible alternate form exists (e.g., a Linux path on
    Linux with no Windows equivalent, or a VolFs path inside WSL2).
    """
    try:
        from dazzle_filekit.utils.compat import is_windows
        import re

        if is_windows():
            # Native form is C:\..., compute WSL form /mnt/c/...
            m = re.match(r"^([a-zA-Z]):[\\/](.*)$", path)
            if m:
                drive = m.group(1).lower()
                rest = m.group(2).replace("\\", "/")
                return f"/mnt/{drive}/{rest}"
        else:
            # Native form might be /mnt/c/... or /c/..., compute Windows form
            m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
            if m:
                drive = m.group(1).upper()
                rest = (m.group(2) or "").replace("/", "\\")
                return f"{drive}:{rest}"
            m = re.match(r"^/([a-zA-Z])(/.*)?$", path)
            if m and len(m.group(1)) == 1:
                drive = m.group(1).upper()
                rest = (m.group(2) or "").replace("/", "\\")
                return f"{drive}:{rest}"
    except Exception:
        pass
    return None


@dataclass
class TrashEntry:
    """A single file/dir entry within a trash folder."""
    original_path: str                          # Native path for current platform
    original_name: str
    file_type: str
    link_target: Optional[str] = None
    link_broken: bool = False
    link_count: int = 1
    is_dir: bool = False
    content_preserved: bool = True
    content_path: Optional[str] = None
    delete_method: str = ""
    stat: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    # WSL dual-path support: original_path_alt holds the other platform's form
    # (e.g., /mnt/c/... when original_path is C:\..., or vice versa).
    # Populated when operating on WSL DrvFs paths so recovery works from
    # either runtime.
    original_path_alt: Optional[str] = None
    # Copy-first record: where the hash-verified second copy is, and what was
    # verified. None when no copy was made.
    copy_first: Optional[Dict[str, Any]] = None
    # Size in bytes of a staged FILE as it sat in the trash when staged.
    # None for folders, links, and batches written before 0.10.39.
    staged_size: Optional[int] = None


@dataclass
class PlannedItem:
    """One thing a batch will do, decided before anything moves.

    The preview prints these and the real run performs exactly these, so the
    two cannot drift apart.
    """
    path: str
    classification: Classification
    action: str                       # "rename" (into the trash) | "remove-link"
    staged_name: Optional[str]        # "/"-joined place under content/ (set by the layout); None if nothing is placed there
    copy_first: bool = False          # a verified copy is made before the move
    metadata_only: bool = False       # a link recorded in the manifest only
    via_link: Optional[str] = None    # this item is what that link points at (delete-through)
    survey: Dict[str, Any] = field(default_factory=dict)
    link_target_entries: Optional[int] = None   # a link's target: entry count at plan time


@dataclass
class TrashPlan:
    """What ``TrashStore.execute`` will do. ``refusals`` non-empty means
    nothing may move."""
    items: List[PlannedItem] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    store_path: str = ""
    is_per_volume: bool = False
    scopes: tuple = ()
    copy_first: bool = False
    copy_first_max: int = DEFAULT_MAX_BYTES
    copy_first_bytes: int = 0
    # One per drive or root in the batch: {"native": <common parent folder>,
    # "key": <its portable key>}. Items are stored relative to these.
    bases: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def describe(self) -> List[str]:
        """The preview: one block of lines per item, then where it all goes."""
        lines: List[str] = []
        for item in self.items:
            c = item.classification
            lines.append(f"  {item.path}")
            kind = c.file_type.value
            if c.link_target:
                kind += f" -> {c.link_target}"
            lines.append(f"    kind:  {kind}")
            if item.action == "remove-link":
                what = "remove the link only; what it points at is not touched"
                if any(o.via_link == item.path for o in self.items):
                    what = ("remove the link; what it points at is ALSO staged "
                            "(delete-through)")
                lines.append(f"    does:  {what}")
            else:
                does = "move into the trash"
                if item.copy_first:
                    does = "copy and verify (SHA-256), then move into the trash"
                if item.via_link:
                    does += f" -- reached through the link {item.via_link}"
                lines.append(f"    does:  {does}")
                if item.survey:
                    from ._copyfirst import format_survey
                    lines.append("    holds: "
                                 + format_survey(item.survey, c.is_dir))
        for path in self.missing:
            lines.append(f"  {path}")
            lines.append("    does:  nothing -- this path does not exist")
        if self.store_path:
            lines.append(f"  trash store: {self.store_path}")
        return lines


@dataclass
class TrashFolder:
    """A timestamped trash folder containing one or more entries."""
    folder_name: str
    folder_path: str
    deleted_at: datetime.datetime
    entries: List[TrashEntry] = field(default_factory=list)
    manifest: Optional[Dict[str, Any]] = None
    # Why the manifest could not be read ("missing", or the parse error).
    # A folder in this state is NOT an empty batch: its content/ may hold the
    # only copy of something, with nothing saying where it came from.
    manifest_error: Optional[str] = None

    @property
    def state(self) -> str:
        """``complete``, ``staging`` (a run that did not finish), or
        ``unknown`` (no readable manifest). Batches written before the state
        field existed are ``complete``."""
        if self.manifest is None:
            return "unknown"
        return self.manifest.get("state", "complete")

    @property
    def age(self) -> datetime.timedelta:
        return datetime.datetime.now() - self.deleted_at


@dataclass
class TrashResult:
    """Result of a trash operation (one or more files deleted)."""
    success: bool
    folder_name: str
    folder_path: str
    entries: List[TrashEntry] = field(default_factory=list)
    staging_results: List[StagingResult] = field(default_factory=list)
    delete_results: List[DeleteResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # True when the plan was refused: nothing moved and no batch folder exists.
    refused: bool = False
    staged_count: int = 0
    not_staged_count: int = 0
    # Checks made AFTER the moves: an original that still exists, a staged
    # item that is not where it should be, a link's target that changed.
    postcondition_failures: List[str] = field(default_factory=list)
    plan: Optional[TrashPlan] = None


@dataclass
class StoreStats:
    """Statistics about the trash store."""
    total_entries: int = 0
    total_folders: int = 0
    total_size_bytes: int = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None
    store_path: str = ""


class TrashStore:
    """Manages the safedel trash store."""

    def __init__(self, store_path: Optional[str] = None,
                 registry_path: Optional[str] = None,
                 junction_registry_path: Optional[str] = None):
        self.store_path = store_path or get_trash_dir()
        self.registry_path = registry_path  # None = global default
        # The junction registry (links recorded by the tools that create
        # them). With an explicit registry_path -- test isolation -- nothing
        # under the home folder is read unless a path is given here.
        if junction_registry_path is None and registry_path is None:
            junction_registry_path = os.path.join(
                os.path.expanduser("~"), ".safedel", "junctions.json")
        self.junction_registry_path = junction_registry_path
        self.platform_info = detect_platform()

    def ensure_store_exists(self):
        """Create the trash store directory if it doesn't exist."""
        if not is_usable_store_root(self.store_path):
            raise ValueError(
                f"trash store path is not an absolute path on this platform: "
                f"{self.store_path!r}"
            )
        os.makedirs(self.store_path, exist_ok=True)

    # -- plan, then execute --------------------------------------------------

    def plan(
        self,
        paths: List[str],
        *,
        scopes=(),
        copy_first: bool = False,
        copy_first_max: Optional[int] = None,
    ) -> TrashPlan:
        """Decide everything a batch will do, and change nothing.

        Classifies each path, expands delete-through items, runs every check
        that can refuse the batch, chooses the store, and records what the
        post-condition check will need. No folder is created, no registry is
        written. The preview prints the result; ``execute`` performs exactly
        it.

        Refusals (any one means nothing moves):
        - none of the given paths exists;
        - two items would take the same name in the batch;
        - an item is the destination of a known junction or directory
          symlink, and the matching ``delete-through-*`` scope was not given;
        - a ``delete-through-*`` scope does not match the link types given;
        - copy-first is on and the batch is larger than its limit;
        - the store path is not absolute on this platform.
        """
        scopes = tuple(scopes or ())
        plan = TrashPlan(
            scopes=scopes,
            copy_first=bool(copy_first),
            copy_first_max=(DEFAULT_MAX_BYTES if copy_first_max is None
                            else int(copy_first_max)),
            store_path=self.store_path,
        )

        classifications = [classify(p) for p in paths]
        plan.missing = [c.path for c in classifications if not c.exists]
        existing = [c for c in classifications if c.exists]
        if not existing:
            plan.refusals.append(
                "nothing to delete: none of the given paths exists")
            return plan

        want_j = DELETE_THROUGH_JUNCTION in scopes
        want_s = DELETE_THROUGH_SYMLINK in scopes
        used_j = used_s = False

        for c in existing:
            is_j = c.file_type == FileType.JUNCTION
            is_s = c.file_type in _SYMLINK_TYPES
            if is_j and want_s and not want_j:
                plan.refusals.append(
                    f"{c.path} is a junction, but the scope given was "
                    f"{DELETE_THROUGH_SYMLINK}; use {DELETE_THROUGH_JUNCTION} "
                    f"to delete what a junction points at")
                continue
            if is_s and want_j and not want_s:
                plan.refusals.append(
                    f"{c.path} is a symlink, but the scope given was "
                    f"{DELETE_THROUGH_JUNCTION}; use {DELETE_THROUGH_SYMLINK} "
                    f"to delete what a symlink points at")
                continue
            if (is_j and want_j) or (is_s and want_s):
                target = classify(c.link_target) if c.link_target else None
                if target is None or not target.exists:
                    plan.refusals.append(
                        f"{c.path}: delete-through was asked for, but what the "
                        f"link points at does not exist "
                        f"({c.link_target or 'no target recorded'})")
                    continue
                if target.file_type in _LINK_TYPES:
                    plan.refusals.append(
                        f"{c.path} points at another link ({target.path}); "
                        f"name the real folder or file directly")
                    continue
                plan.items.append(PlannedItem(
                    path=target.path, classification=target, action="rename",
                    staged_name=os.path.basename(target.path),
                    via_link=c.path))
                plan.items.append(PlannedItem(
                    path=c.path, classification=c, action="remove-link",
                    staged_name=None, metadata_only=True))
                used_j = used_j or is_j
                used_s = used_s or is_s
                continue
            plan.items.append(self._plan_item(c))

        # Target-side defence: a real folder that a known link points at.
        for item in plan.items:
            c = item.classification
            if (item.action != "rename" or item.via_link
                    or c.file_type not in (FileType.REGULAR_DIR,
                                           FileType.EMPTY_DIR)):
                continue
            hits = links_pointing_at(item.path, self.junction_registry_path)
            if not hits:
                continue
            kinds = {h.link_type for h in hits}
            allowed = all((k == "junction" and want_j)
                          or (k == "symlink" and want_s) for k in kinds)
            if allowed:
                used_j = used_j or "junction" in kinds
                used_s = used_s or "symlink" in kinds
                continue
            first = hits[0]
            scope = (DELETE_THROUGH_JUNCTION if first.link_type == "junction"
                     else DELETE_THROUGH_SYMLINK)
            more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
            plan.refusals.append(
                f"{item.path} is what the {first.link_type} "
                f"{first.link_path}{more} points at; deleting it removes the "
                f"real data behind that link. To remove only the link, name "
                f"the link itself. To delete this folder anyway, pass "
                f"--yes {scope}")

        if want_j and not used_j and not plan.refusals:
            plan.refusals.append(
                f"--yes {DELETE_THROUGH_JUNCTION} was given, but no path is a "
                f"junction or a junction's destination")
        if want_s and not used_s and not plan.refusals:
            plan.refusals.append(
                f"--yes {DELETE_THROUGH_SYMLINK} was given, but no path is a "
                f"symlink or a symlink's destination")

        # Where each item sits under content/: its path relative to the
        # batch's common base, so two items with one name do not collide.
        self._plan_layout(plan)

        # Belt and braces: no two items may land on one place under content/
        # (compared after Unicode normalisation and case folding, which the
        # layout planner does not do).
        by_name: Dict[str, List[str]] = {}
        for item in plan.items:
            if item.staged_name:
                by_name.setdefault(_name_key(item.staged_name), []).append(
                    item.path)
        clashes = {k: v for k, v in by_name.items() if len(v) > 1}
        if clashes:
            detail = "; ".join(
                f"'{k}' ({', '.join(v)})" for k, v in clashes.items())
            plan.refusals.append(
                f"two or more items would be stored at one place in the "
                f"batch: {detail}. Nothing was moved; delete them in "
                f"separate calls")

        # What each item holds (for the preview and the copy-first limit),
        # and, for a link, how many entries its target has right now.
        for item in plan.items:
            item.survey = survey(item.path)
            c = item.classification
            if (item.action == "remove-link" and c.link_target
                    and not c.link_broken):
                try:
                    if os.path.isdir(c.link_target):
                        item.link_target_entries = len(
                            os.listdir(c.link_target))
                except OSError:
                    pass

        # Copy-first: which items get the second copy, and is it too much?
        if plan.copy_first:
            for item in plan.items:
                c = item.classification
                if (item.action == "rename" and c.content_preservable
                        and c.file_type != FileType.UNKNOWN):
                    item.copy_first = True
                    plan.copy_first_bytes += item.survey.get("bytes", 0)
            if plan.copy_first_bytes > plan.copy_first_max:
                plan.refusals.append(
                    f"copy-first would copy "
                    f"{format_bytes(plan.copy_first_bytes)}, over its limit "
                    f"of {format_bytes(plan.copy_first_max)}. To stage "
                    f"without the extra copy pass --yes no-copy-first; to "
                    f"raise the limit pass --copy-first-max <size>")

        # Where it goes. Per-volume first (a same-volume rename keeps every
        # timestamp, ACL and stream); the central store otherwise. An explicit
        # registry_path means the caller owns the store (test isolation).
        first = plan.items[0].path if plan.items else existing[0].path
        if self.registry_path is None:
            plan.store_path, plan.is_per_volume = resolve_trash_store(
                first, self.store_path, create=False)
        if not is_usable_store_root(plan.store_path):
            plan.refusals.append(
                f"the trash store path is not an absolute path on this "
                f"platform: {plan.store_path!r}")
            return plan

        self._plan_warnings(plan)
        return plan

    def _plan_layout(self, plan: TrashPlan) -> None:
        """Give every item its place under ``content/`` (common-base layout).

        The batch's items are laid out relative to their common parent
        folder, one base per drive or root, so ``a/docs`` and ``b/docs`` are
        stored as ``a/docs`` and ``b/docs`` instead of fighting over
        ``docs``. A batch of one item keeps just its name. The planner
        (``dazzle_filekit.batch_layout.plan_batch``) reads no disk and
        resolves nothing.

        What it reports becomes part of the plan: a path given twice is kept
        once and an item inside another item is dropped (warnings; the
        dropped item leaves ``plan.items``); an item inside a link that is
        also in the batch, a drive root, or two items that still land on one
        place are refusals.
        """
        if not plan.items:
            return
        try:
            from dazzle_filekit.batch_layout import plan_batch
        except ImportError:
            plan.refusals.append(
                "this version of safedel needs dazzle-filekit 0.5.0 or later "
                "(its batch layout planner is missing); nothing was moved")
            return

        if sys.platform == "win32":
            flavor = "windows"
        else:
            flavor = "wsl" if self.platform_info.is_wsl else "posix"
        # Only items that occupy a place under content/ are laid out. A
        # junction (recorded in the manifest only) and the link of a
        # delete-through pair take no place, so they must not stretch the
        # common base: deleting through one link stores its target under the
        # target's own name.
        placed_items = [i for i in plan.items if i.staged_name is not None]
        unplaced = [i for i in plan.items if i.staged_name is None]

        # ... but an item INSIDE such a link is still refused: the link would
        # be removed as a link, and the item is in what it points at.
        def _inside(child: str, parent: str) -> bool:
            a, b = child, parent.rstrip("\\/")
            if sys.platform == "win32":
                a, b = a.replace("/", "\\").casefold(), b.replace("/", "\\").casefold()
            return a.startswith(b + os.sep)
        for link in unplaced:
            for other in plan.items:
                if other is not link and other.via_link != link.path \
                        and _inside(other.path, link.path):
                    plan.refusals.append(
                        f"{other.path} is inside {link.path}, which is a "
                        f"link: the link would be removed as a link, and "
                        f"this item is in what it points at. Name one or "
                        f"the other")
        if not placed_items:
            return

        link_paths = {i.path for i in placed_items
                      if i.classification.file_type in _LINK_TYPES}
        layout = plan_batch([i.path for i in placed_items], flavor=flavor,
                            is_link=lambda p: p in link_paths)

        for message in layout.errors:
            plan.refusals.append(f"the batch cannot be laid out: {message}")
        plan.warnings.extend(layout.warnings)
        plan.bases = list(layout.bases)
        if len(layout.bases) > 1:
            roots = ", ".join(str(b.get("native")) for b in layout.bases)
            plan.warnings.append(
                f"this batch spans {len(layout.bases)} drives or roots "
                f"({roots}) and one trash store holds it: items that are not "
                f"on the store's drive are copied there, not moved")

        # Match the planner's items back to ours, in order. A path given
        # twice is in the layout once; the second PlannedItem is dropped.
        placed = {b.original: b.relpath for b in layout.items}
        kept: List[PlannedItem] = []
        for item in plan.items:
            if item.staged_name is None:
                kept.append(item)       # takes no place; not laid out
                continue
            relpath = placed.pop(item.path, None)
            if relpath is None:
                if layout.errors:
                    kept.append(item)   # refused anyway; keep it visible
                continue
            item.staged_name = relpath
            kept.append(item)
        plan.items = kept

    def _plan_item(self, c: Classification) -> PlannedItem:
        """The planned act for one ordinary (not delete-through) item."""
        name = os.path.basename(c.path)
        if c.file_type == FileType.JUNCTION:
            # a junction is recorded in the manifest; nothing lands in content/
            return PlannedItem(path=c.path, classification=c,
                               action="remove-link", staged_name=None,
                               metadata_only=True)
        if c.file_type in _LINK_TYPES:
            # a symlink is recreated under content/, so it occupies its name
            return PlannedItem(path=c.path, classification=c,
                               action="remove-link", staged_name=name)
        return PlannedItem(path=c.path, classification=c, action="rename",
                           staged_name=name)

    def _plan_warnings(self, plan: TrashPlan) -> None:
        """Disk space, cross-device and alternate-stream warnings."""
        movable = [i.path for i in plan.items
                   if i.action == "rename"
                   and i.classification.content_preservable]
        if not movable:
            return
        total_size = sum(i.survey.get("bytes", 0) for i in plan.items
                         if i.action == "rename")
        store_probe = _nearest_existing(plan.store_path)
        space_warning = check_disk_space(
            store_probe, total_size + plan.copy_first_bytes)
        if space_warning:
            plan.warnings.append(space_warning)

        cross_device = not is_same_volume(movable[0], plan.store_path)
        one_gb = 1024 * 1024 * 1024
        if cross_device and total_size > one_gb:
            size_gb = total_size / one_gb
            plan.warnings.append(
                f"Cross-device staging: {size_gb:.1f}GB will be copied "
                f"(not renamed). This may be slow and some metadata "
                f"(creation time, ADS) will not be preserved in the copy. "
                f"Original metadata is recorded in the manifest."
            )
        elif cross_device:
            plan.warnings.append(
                "Cross-device staging: the trash store is on another volume, "
                "so items are copied and then removed (not renamed); creation "
                "time and alternate streams are not carried by the copy.")

        if cross_device and sys.platform == "win32":
            ads_files = []
            for p in movable:
                try:
                    streams = detect_alternate_streams(p)
                    if streams:
                        ads_files.append((p, streams))
                except Exception:
                    pass
            if ads_files:
                names = ", ".join(
                    f"{os.path.basename(p)}({len(s)} streams)"
                    for p, s in ads_files[:3]
                )
                if len(ads_files) > 3:
                    names += f", and {len(ads_files) - 3} more"
                plan.warnings.append(
                    f"NTFS alternate data streams detected on "
                    f"cross-device staging: {names}. These streams will "
                    f"NOT be preserved in the copy (stream names are "
                    f"recorded in the manifest)."
                )

    def trash(
        self,
        paths: List[str],
        dry_run: bool = False,
        *,
        scopes=(),
        copy_first: bool = False,
        copy_first_max: Optional[int] = None,
    ) -> TrashResult:
        """Stage files for deletion and remove originals.

        ``plan()`` followed by ``execute()``. With ``dry_run`` the plan is
        returned inside the result and nothing is touched. A refused plan
        returns ``success=False, refused=True`` with the reasons in
        ``errors``, and no batch folder exists.

        Args:
            paths: list of filesystem paths to delete
            dry_run: if True, plan and report but don't touch files
            scopes: ``--yes`` scopes in force (see ``_scopes``)
            copy_first: make a hash-verified copy of each item before moving
            copy_first_max: byte limit for that copy (default 10 MB)

        Returns:
            TrashResult with full details of what happened
        """
        plan = self.plan(paths, scopes=scopes, copy_first=copy_first,
                         copy_first_max=copy_first_max)
        if dry_run:
            return TrashResult(
                success=not plan.refused,
                refused=plan.refused,
                folder_name="",
                folder_path="",
                entries=[self._build_entry(i.classification, "", False)
                         for i in plan.items],
                errors=list(plan.refusals),
                warnings=list(plan.warnings)
                + ["DRY RUN: No files were modified."],
                not_staged_count=len(plan.items) + len(plan.missing),
                plan=plan,
            )
        return self.execute(plan)

    def execute(self, plan: TrashPlan) -> TrashResult:
        """Perform a plan: create the batch, move each item, check the result.

        The manifest is written BEFORE anything moves (``state: staging``)
        and rewritten after each item, so a run that is interrupted leaves a
        manifest that maps every item already moved back to where it came
        from. It is marked ``complete`` at the end.
        """
        if plan.refused:
            return TrashResult(
                success=False, refused=True, folder_name="", folder_path="",
                errors=list(plan.refusals), warnings=list(plan.warnings),
                not_staged_count=len(plan.items) + len(plan.missing),
                plan=plan,
            )

        if self.registry_path is None:
            commit_trash_store(plan.items[0].path, plan.store_path,
                               plan.is_per_volume)
        else:
            os.makedirs(plan.store_path, exist_ok=True)

        folder_name = claim_unique_folder(plan.store_path)
        folder_path = os.path.join(plan.store_path, folder_name)
        content_dir = os.path.join(folder_path, "content")
        os.makedirs(content_dir, exist_ok=True)
        manifest_path = os.path.join(folder_path, "manifest.json")

        entries: List[TrashEntry] = []
        staging_results: List[StagingResult] = []
        delete_results: List[DeleteResult] = []
        errors: List[str] = []
        warnings: List[str] = list(plan.warnings)
        staged: List[PlannedItem] = []
        not_staged = 0

        for path in plan.missing:
            errors.append(f"Skipping non-existent path: {path}")
            not_staged += 1

        def save(state: str) -> None:
            manifest = self._build_manifest(folder_name, entries)
            manifest["state"] = state
            manifest["scopes"] = list(plan.scopes)
            # Items sit under content/ at their path relative to these bases.
            # A manifest without content_layout is a flat batch written
            # before 0.10.39 (every item directly under content/).
            manifest["content_layout"] = CONTENT_LAYOUT
            manifest["bases"] = list(plan.bases)
            self._save_manifest(manifest, manifest_path)

        def under(root: str, relpath: str) -> str:
            return os.path.join(root, *relpath.split("/"))

        save("staging")

        for item in plan.items:
            c = item.classification

            # Collect metadata before any changes
            entry = self._build_entry(c, content_dir, collect_meta=True)
            entries.append(entry)

            copy_dest = None
            if item.copy_first and item.staged_name:
                copy_dest = under(os.path.join(folder_path, "copy-first"),
                                  item.staged_name)
                try:
                    record = _make_copy_first(item.path, copy_dest, c.is_dir)
                except OSError as e:
                    errors.append(
                        f"copy-first failed for {item.path}: {e}; "
                        f"left in place")
                    not_staged += 1
                    save("staging")
                    continue
                record["path"] = "copy-first/" + item.staged_name
                entry.copy_first = record

            # Stage to trash
            if item.metadata_only and c.file_type != FileType.JUNCTION:
                # delete-through: the link is recorded, not recreated (its
                # target is in this same batch under the same name)
                sr = StagingResult(
                    success=True, source_path=c.path,
                    dest_path=os.path.join(content_dir,
                                           os.path.basename(c.path)),
                    method="metadata_only")
            elif item.staged_name:
                sr = _stage_item(c.path, under(content_dir, item.staged_name), c)
            else:
                # a junction: recorded in the manifest, nothing under content/
                sr = stage_to_trash(c.path, content_dir, c)
            staging_results.append(sr)

            if not sr.success:
                errors.append(f"Failed to stage {c.path}: {sr.error}")
                not_staged += 1
                if copy_dest:
                    # the original never moved; the copy would be a
                    # second, unlisted copy of something still in place
                    _discard_copy(copy_dest)
                    entry.copy_first = None
                save("staging")
                continue

            if sr.warnings:
                warnings.extend(sr.warnings)

            # Update entry with actual content path
            if sr.method != "metadata_only" and item.staged_name:
                # always "/"-joined, whatever the platform
                entry.content_path = "content/" + item.staged_name
                if not c.is_dir and c.file_type not in _LINK_TYPES:
                    try:
                        entry.staged_size = os.lstat(
                            under(content_dir, item.staged_name)).st_size
                    except OSError:
                        pass
            elif sr.method != "metadata_only":
                entry.content_path = "content/" + os.path.basename(c.path)

            item_ok = True
            # Delete original (only if staging used copy, not rename)
            if sr.method == "copy":
                dr = safe_delete(c.path, c)
                delete_results.append(dr)
                if not dr.success:
                    errors.append(f"Failed to delete {c.path}: {dr.error}")
                    item_ok = False
                if dr.warnings:
                    warnings.extend(dr.warnings)
            elif sr.method == "rename":
                # Rename already moved the file -- nothing to delete
                delete_results.append(DeleteResult(
                    success=True, path=c.path, method_used="rename (moved to trash)"
                ))
            elif sr.method in ("metadata_only", "symlink_recreate"):
                # For links: delete the link itself
                dr = safe_delete(c.path, c)
                delete_results.append(dr)
                if not dr.success:
                    errors.append(f"Failed to remove link {c.path}: {dr.error}")
                    item_ok = False
                if dr.warnings:
                    warnings.extend(dr.warnings)

            if item_ok:
                staged.append(item)
            else:
                not_staged += 1
            save("staging")

        post = self._check_postconditions(plan, staged, folder_path)
        save("complete")

        return TrashResult(
            success=not errors and not post,
            folder_name=folder_name,
            folder_path=folder_path,
            entries=entries,
            staging_results=staging_results,
            delete_results=delete_results,
            errors=errors,
            warnings=warnings,
            staged_count=len(staged),
            not_staged_count=not_staged,
            postcondition_failures=post,
            plan=plan,
        )

    def _check_postconditions(self, plan: TrashPlan,
                              staged: List[PlannedItem],
                              folder_path: str) -> List[str]:
        """After the moves: is the disk in the state the plan promised?

        For every item reported as staged: the original is gone; content
        that was moved is where the manifest says; and a link's target still
        exists with the same number of entries it had when planned (unless
        the target was itself part of this batch). Uses ``lexists`` and
        ``listdir`` only -- nothing is resolved.
        """
        failures: List[str] = []
        through = {i.via_link for i in plan.items if i.via_link}
        for item in staged:
            c = item.classification
            if os.path.lexists(item.path):
                failures.append(
                    f"{item.path} still exists (it was not removed, or "
                    f"something re-created it)")
            if item.action == "rename" and item.staged_name:
                where = os.path.join(folder_path, "content",
                                     *item.staged_name.split("/"))
                if not os.path.lexists(where):
                    failures.append(
                        f"{item.path} was reported staged but is not at "
                        f"{where}")
            if (item.action == "remove-link" and item.path not in through
                    and item.link_target_entries is not None):
                try:
                    now = len(os.listdir(c.link_target))
                except OSError:
                    failures.append(
                        f"the target of the link {item.path} "
                        f"({c.link_target}) can no longer be listed")
                    continue
                if now != item.link_target_entries:
                    failures.append(
                        f"the target of the link {item.path} "
                        f"({c.link_target}) had {item.link_target_entries} "
                        f"entries before and has {now} now")
        return failures

    def list_entries(
        self,
        pattern: Optional[str] = None,
        age_filter: Optional[str] = None,
    ) -> List[TrashFolder]:
        """List trash folders matching a pattern and/or age filter.

        Scans both central and per-volume trash stores.
        """
        results = []
        for store_path in self._all_store_paths():
            folder_names = match_trash_folders(store_path, pattern, age_filter)
            for name in folder_names:
                folder = self._load_trash_folder(name, store_path)
                if folder:
                    results.append(folder)
        # Sort by timestamp across all stores
        results.sort(key=lambda f: f.deleted_at)
        return results

    def get_folder(self, folder_name: str) -> Optional[TrashFolder]:
        """Load a specific trash folder by name, searching all stores."""
        for store_path in self._all_store_paths():
            folder = self._load_trash_folder(folder_name, store_path)
            if folder:
                return folder
        return None

    def get_stats(self) -> StoreStats:
        """Get statistics about the trash store (all stores combined)."""
        stats = StoreStats(store_path=self.store_path)

        all_folders = self.list_entries()
        stats.total_folders = len(all_folders)

        if all_folders:
            stats.oldest = all_folders[0].folder_name
            stats.newest = all_folders[-1].folder_name

        total_entries = 0
        total_size = 0
        for folder in all_folders:
            total_entries += len(folder.entries)
            for e in folder.entries:
                if e.stat:
                    total_size += e.stat.get("st_size", 0)

        stats.total_entries = total_entries
        stats.total_size_bytes = total_size
        return stats

    def remove_folder(self, folder_name: str,
                      folder_path: Optional[str] = None) -> bool:
        """Permanently remove a trash folder.

        Pass ``folder_path`` (a ``TrashFolder.folder_path``) to remove that
        exact folder. By name alone, every store is searched and the FIRST
        folder with that name is removed -- and two stores can hold batches
        with the same name (names have one-second resolution and each store
        picks its own), so a by-name removal after recovering a batch from
        one store could delete an unrelated, unrecovered batch in another.
        Callers that hold a ``TrashFolder`` must pass its path.
        """
        import shutil as _shutil
        if folder_path is not None:
            if os.path.basename(folder_path.rstrip("\\/")) != folder_name:
                return False
            if not os.path.isdir(folder_path) or os.path.islink(folder_path):
                return False
            try:
                _shutil.rmtree(folder_path)
                return True
            except OSError:
                return False
        for store_path in self._all_store_paths():
            candidate = os.path.join(store_path, folder_name)
            if os.path.isdir(candidate):
                try:
                    _shutil.rmtree(candidate)
                    return True
                except OSError:
                    return False
        return False

    @staticmethod
    def leftover_content(folder: "TrashFolder") -> List[str]:
        """Paths still present under a batch's ``content/`` (files, links,
        and non-empty folders are reported by their files). Empty folders do
        not count. Used before a batch folder is removed after recovery: a
        folder that still holds content is never removed as "recovered"."""
        left: List[str] = []
        content = os.path.join(folder.folder_path, "content")
        stack = [content]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                            try:
                                reparse = bool(
                                    getattr(entry.stat(follow_symlinks=False),
                                            "st_file_attributes", 0) & 0x400)
                            except OSError:
                                reparse = False
                            if reparse:
                                left.append(entry.path)
                            else:
                                stack.append(entry.path)
                        else:
                            left.append(entry.path)
            except OSError:
                continue
        return left

    def _all_store_paths(self) -> List[str]:
        """Get all known trash store paths (central + per-volume)."""
        return get_all_trash_paths(self.store_path, self.registry_path)

    # -- Internal methods --

    def _build_entry(
        self, c: Classification, content_dir: str, collect_meta: bool
    ) -> TrashEntry:
        """Build a TrashEntry from a Classification."""
        meta = None
        stat_dict = None

        if collect_meta:
            try:
                raw_meta = collect_file_metadata(c.path)
                meta = metadata_to_json(raw_meta)
            except Exception:
                meta = None

            try:
                st = os.lstat(c.path)
                stat_dict = {
                    "st_size": st.st_size,
                    "st_mtime": st.st_mtime,
                    "st_atime": st.st_atime,
                    "st_ctime": st.st_ctime,
                    "st_mode": st.st_mode,
                    "st_nlink": st.st_nlink,
                    "st_ino": getattr(st, "st_ino", 0),
                }
                # Windows-specific
                if hasattr(st, "st_file_attributes"):
                    stat_dict["st_file_attributes"] = st.st_file_attributes
                # macOS/BSD birthtime
                if hasattr(st, "st_birthtime"):
                    stat_dict["st_birthtime"] = st.st_birthtime
                # Unix uid/gid
                if hasattr(st, "st_uid"):
                    stat_dict["st_uid"] = st.st_uid
                    stat_dict["st_gid"] = st.st_gid
            except OSError:
                stat_dict = None

        # Compute alternate-platform path form for cross-runtime recovery
        # (WSL <-> Windows). Uses filekit's normalize_cross_platform_path.
        alt_path = _compute_alt_path(c.path)

        return TrashEntry(
            original_path=c.path,
            original_path_alt=alt_path,
            original_name=os.path.basename(c.path),
            file_type=c.file_type.value,
            link_target=c.link_target,
            link_broken=c.link_broken,
            link_count=c.link_count,
            is_dir=c.is_dir,
            content_preserved=c.content_preservable,
            delete_method=c.delete_method.value,
            stat=stat_dict,
            metadata=meta,
            warnings=list(c.warnings),
        )

    def _build_manifest(
        self, folder_name: str, entries: List[TrashEntry]
    ) -> Dict[str, Any]:
        """Build the manifest dict for a trash folder."""
        now = datetime.datetime.now()
        return {
            "version": 1,
            "safedel_version": "0.1.0",
            "deleted_at": now.isoformat(),
            "folder_name": folder_name,
            "platform": {
                "system": self.platform_info.system,
                "platform": self.platform_info.platform,
                "is_wsl": self.platform_info.is_wsl,
                "wsl_distro": self.platform_info.wsl_distro,
                "python_version": self.platform_info.python_version,
                "hostname": self.platform_info.hostname,
            },
            "entries": [self._entry_to_dict(e) for e in entries],
        }

    def _entry_to_dict(self, entry: TrashEntry) -> Dict[str, Any]:
        """Convert a TrashEntry to a JSON-serializable dict."""
        return {
            "original_path": entry.original_path,
            "original_path_alt": entry.original_path_alt,
            "original_name": entry.original_name,
            "file_type": entry.file_type,
            "link_target": entry.link_target,
            "link_broken": entry.link_broken,
            "link_count": entry.link_count,
            "is_dir": entry.is_dir,
            "content_preserved": entry.content_preserved,
            "content_path": entry.content_path,
            "delete_method": entry.delete_method,
            "stat": entry.stat,
            "metadata": entry.metadata,
            "warnings": entry.warnings,
            "copy_first": entry.copy_first,
            "staged_size": entry.staged_size,
        }

    def _save_manifest(
        self, manifest: Dict[str, Any], path: str
    ) -> None:
        """Save manifest atomically via dazzle_filekit.operations.atomic_write_json."""
        from dazzle_filekit.operations import atomic_write_json
        atomic_write_json(path, manifest, indent=2, default=str)

    def _load_trash_folder(self, folder_name: str, store_path: Optional[str] = None) -> Optional[TrashFolder]:
        """Load a TrashFolder from disk."""
        folder_path = os.path.join(store_path or self.store_path, folder_name)
        manifest_path = os.path.join(folder_path, "manifest.json")

        if not os.path.isdir(folder_path):
            return None

        dt = parse_folder_datetime(folder_name)
        if dt is None:
            return None

        manifest = None
        manifest_error = None
        entries = []

        if not os.path.isfile(manifest_path):
            manifest_error = "missing"
        else:
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("manifest is not a JSON object")
                manifest = loaded
                for e_dict in manifest.get("entries", []):
                    entries.append(TrashEntry(
                        original_path=e_dict.get("original_path", ""),
                        original_path_alt=e_dict.get("original_path_alt"),
                        original_name=e_dict.get("original_name", ""),
                        file_type=e_dict.get("file_type", "unknown"),
                        link_target=e_dict.get("link_target"),
                        link_broken=e_dict.get("link_broken", False),
                        link_count=e_dict.get("link_count", 1),
                        is_dir=e_dict.get("is_dir", False),
                        content_preserved=e_dict.get("content_preserved", True),
                        content_path=e_dict.get("content_path"),
                        delete_method=e_dict.get("delete_method", ""),
                        stat=e_dict.get("stat"),
                        metadata=e_dict.get("metadata"),
                        warnings=e_dict.get("warnings", []),
                        copy_first=e_dict.get("copy_first"),
                        staged_size=e_dict.get("staged_size"),
                    ))
            except (ValueError, OSError, AttributeError, TypeError) as exc:
                # json.JSONDecodeError is a ValueError. An unreadable
                # manifest is reported, never silently turned into "this
                # batch has no entries".
                manifest = None
                entries = []
                manifest_error = f"unreadable ({exc})"

        return TrashFolder(
            folder_name=folder_name,
            folder_path=folder_path,
            deleted_at=dt,
            entries=entries,
            manifest=manifest,
            manifest_error=manifest_error,
        )
