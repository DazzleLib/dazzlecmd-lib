"""
Volume detection and per-volume trash directory management for safedel.

Per-volume trash enables zero-copy os.rename() staging, which preserves
ALL metadata (creation time, ACLs, ADS, xattrs). When the trash store
is on the same volume as the source, rename is atomic and lossless.

Volume registry at ~/.safedel/volumes.json tracks known volume stores
with stable identifiers (serial number on Windows, filesystem UUID on
Linux/macOS) so ejected volumes can be detected as orphaned.

Trash location per volume:
    Windows local:  <drive>:\\Users\\<username>\\.safedel-trash\\
    Windows net:    Not supported (fallback to central store)
    Linux/macOS:    <mountpoint>/.safedel-trash-<uid>/
    Fallback:       Central store (~/.safedel/trash/ or %LOCALAPPDATA%)

Uses dazzle-filekit for disk utilities and unctools for drive type detection.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Drive detection via dazzlelib packages
from dazzle_filekit.utils.disk import get_disk_usage

if sys.platform == "win32":
    from unctools.detector import get_drive_type, is_network_drive, is_unc_path
    # Drive type constants from unctools
    PATH_TYPE_NETWORK = "network"
    PATH_TYPE_REMOVABLE = "removable"
    PATH_TYPE_FIXED = "fixed"
else:
    # Stubs for non-Windows -- these features are Windows-specific
    def get_drive_type(path):
        return "fixed"
    def is_network_drive(path):
        return False
    def is_unc_path(path):
        return False
    PATH_TYPE_NETWORK = "network"
    PATH_TYPE_REMOVABLE = "removable"
    PATH_TYPE_FIXED = "fixed"


@dataclass
class VolumeInfo:
    """Information about a filesystem volume."""
    device_id: int              # os.stat().st_dev
    mount_point: str            # Drive root or mount point
    volume_serial: Optional[str] = None  # Stable ID (Windows serial, Linux UUID)
    volume_name: Optional[str] = None    # Human-readable label
    filesystem: Optional[str] = None     # NTFS, ext4, APFS, etc.
    drive_type: str = "fixed"   # fixed, network, removable, etc.
    is_network: bool = False
    is_subst: bool = False
    is_removable: bool = False
    is_readonly: bool = False


@dataclass
class VolumeTrashInfo:
    """Registry entry for a per-volume trash store."""
    volume_serial: str
    volume_name: Optional[str]
    mount_point: str
    trash_path: str
    last_seen: str              # ISO timestamp
    entry_count: int = 0
    is_reachable: bool = True


# -- Volume Detection --


def get_volume_info(path: str) -> VolumeInfo:
    """Get volume information for the filesystem containing path.

    Uses ``lstat``: for a junction or symlink the question is which volume
    the LINK is on, not where it points. ``stat`` would follow it.
    """
    st = os.lstat(path)
    device_id = st.st_dev

    if sys.platform == "win32":
        return _get_volume_info_windows(path, device_id)
    else:
        return _get_volume_info_unix(path, device_id)


def _get_volume_info_windows(path: str, device_id: int) -> VolumeInfo:
    """Get volume info on Windows using unctools + ctypes for serial."""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return VolumeInfo(device_id=device_id, mount_point=path)

    root = drive + os.sep
    info = VolumeInfo(device_id=device_id, mount_point=root)

    # Use unctools for drive type detection
    dtype = get_drive_type(root)
    info.drive_type = dtype
    info.is_network = (dtype == PATH_TYPE_NETWORK)
    info.is_removable = (dtype == PATH_TYPE_REMOVABLE)
    info.is_readonly = not os.access(root, os.W_OK)

    # Detect subst drives via QueryDosDeviceW
    info.is_subst = _is_subst_drive_windows(drive)

    # Get volume serial and name via GetVolumeInformationW
    try:
        serial, name, fs = _get_volume_serial_windows(root)
        info.volume_serial = f"{serial:#010x}" if serial else None
        info.volume_name = name
        info.filesystem = fs
    except OSError:
        pass

    return info


def _is_subst_drive_windows(drive_letter: str) -> bool:
    """Check if a drive letter is a SUBST'd path."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        drive = drive_letter.rstrip(":\\/")
        buf = ctypes.create_unicode_buffer(1024)
        result = kernel32.QueryDosDeviceW(drive + ":", buf, 1024)
        if result > 0:
            # SUBST drives have a device path starting with \??\
            return buf.value.startswith("\\??\\")
        return False
    except (AttributeError, OSError):
        return False


def _get_volume_serial_windows(root: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Get volume serial number, name, and filesystem type on Windows."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    vol_name = ctypes.create_unicode_buffer(256)
    vol_serial = wintypes.DWORD()
    max_len = wintypes.DWORD()
    flags = wintypes.DWORD()
    fs_name = ctypes.create_unicode_buffer(256)

    ok = kernel32.GetVolumeInformationW(
        root, vol_name, 256,
        ctypes.byref(vol_serial), ctypes.byref(max_len),
        ctypes.byref(flags), fs_name, 256
    )
    if ok:
        return vol_serial.value, vol_name.value or None, fs_name.value or None
    return None, None, None


def _get_volume_info_unix(path: str, device_id: int) -> VolumeInfo:
    """Get volume info on Linux/macOS."""
    mount_point = _find_mount_point(path)

    info = VolumeInfo(device_id=device_id, mount_point=mount_point)
    info.is_readonly = not os.access(mount_point, os.W_OK)

    # Detect network mounts
    info.is_network = _is_network_mount_unix(mount_point)
    info.drive_type = PATH_TYPE_NETWORK if info.is_network else PATH_TYPE_FIXED

    # Try to get filesystem UUID on Linux
    if sys.platform == "linux":
        info.volume_serial = _get_fs_uuid_linux(mount_point)

    return info


def _find_mount_point(path: str) -> str:
    """Find the mount point for a given path."""
    path = os.path.abspath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _get_fs_uuid_linux(mount_point: str) -> Optional[str]:
    """Get filesystem UUID on Linux via /dev/disk/by-uuid/."""
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mount_point:
                    device = parts[0]
                    break
            else:
                return None

        uuid_dir = "/dev/disk/by-uuid"
        if os.path.isdir(uuid_dir):
            for uuid_name in os.listdir(uuid_dir):
                link_target = os.path.realpath(os.path.join(uuid_dir, uuid_name))
                if link_target == os.path.realpath(device):
                    return uuid_name
    except (OSError, ValueError):
        pass
    return None


def _is_network_mount_unix(mount_point: str) -> bool:
    """Check if a mount point is a network filesystem."""
    network_fs = {"nfs", "nfs4", "cifs", "smbfs", "afp", "fuse.sshfs"}
    try:
        if os.path.isfile("/proc/mounts"):
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == mount_point:
                        return parts[2] in network_fs
    except OSError:
        pass
    return False


# -- Per-Volume Trash Path Resolution --


def is_usable_store_root(path: str) -> bool:
    """True if ``path`` can safely be the root a trash store is made under.

    A store path must be absolute ON THE RUNNING PLATFORM. On POSIX a
    Windows-style path (``C:\\Users\\me``, ``C:/``) is not absolute: it is a
    relative name, and ``os.makedirs`` would quietly create a folder
    literally called ``C:`` under the current directory. That is how stray
    ``C:/.safedel-trash-<uid>`` folders appeared inside source checkouts
    (2026-11-09, and one dated 2026-04-10).
    """
    if not path or not os.path.isabs(path):
        return False
    if os.name != "nt" and len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return False
    return True


def get_per_volume_trash_path(
    volume: VolumeInfo, username: Optional[str] = None, create: bool = True
) -> Optional[str]:
    """Determine the per-volume trash path for a given volume.

    Returns None if per-volume trash is not appropriate (network drives,
    read-only volumes, subst drives, or a mount point that is not an
    absolute path on this platform).

    ``create=False`` only computes the path: nothing is created, so a
    preview can ask where a batch would go without changing the disk.
    """
    if volume.is_network or volume.is_readonly or volume.is_subst:
        return None

    if sys.platform == "win32":
        return _per_volume_path_windows(volume, username, create=create)
    else:
        return _per_volume_path_unix(volume, create=create)


def _per_volume_path_windows(
    volume: VolumeInfo, username: Optional[str] = None, create: bool = True
) -> Optional[str]:
    """Per-volume trash: <drive>\\Users\\<username>\\.safedel-trash\\"""
    if not username:
        username = os.environ.get("USERNAME", "")
        if not username:
            try:
                username = os.getlogin()
            except OSError:
                return None

    drive = volume.mount_point.rstrip(os.sep)
    if not os.path.splitdrive(drive)[0]:
        return None  # not a drive root: never build a store on a relative name
    user_dir = os.path.join(drive, os.sep, "Users", username)

    if os.path.isdir(user_dir):
        return os.path.join(user_dir, ".safedel-trash")

    # Fallback: try root of drive
    root_path = os.path.join(drive, os.sep, ".safedel-trash")
    if not create:
        return root_path if os.access(drive + os.sep, os.W_OK) else None
    try:
        os.makedirs(root_path, exist_ok=True)
        return root_path
    except PermissionError:
        return None


def _per_volume_path_unix(volume: VolumeInfo, create: bool = True) -> Optional[str]:
    """Per-volume trash: <mountpoint>/.safedel-trash-<uid>/"""
    if not is_usable_store_root(volume.mount_point):
        return None
    uid = os.getuid()
    trash_dir = os.path.join(volume.mount_point, f".safedel-trash-{uid}")

    if not create:
        if os.path.isdir(trash_dir) or os.access(volume.mount_point, os.W_OK):
            return trash_dir
        return None
    try:
        if not os.path.exists(trash_dir):
            os.makedirs(trash_dir, mode=0o700)
        return trash_dir
    except PermissionError:
        return None


def _nearest_existing(path: str) -> str:
    """``path`` itself if it exists, else its nearest existing ancestor."""
    current = os.path.abspath(path)
    while not os.path.lexists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def is_same_volume(path1: str, path2: str) -> bool:
    """Check if two paths are on the same volume.

    ``path1`` (the item) is examined with ``lstat`` so a link is placed on
    the volume the link itself is on. ``path2`` (a store) may not exist yet;
    its nearest existing ancestor stands in for it.
    """
    try:
        return (os.lstat(path1).st_dev
                == os.stat(_nearest_existing(path2)).st_dev)
    except OSError:
        return False


# -- Volume Registry --


def get_registry_path() -> str:
    """Path to the volume registry file."""
    return os.path.join(os.path.expanduser("~"), ".safedel", "volumes.json")


def load_registry(registry_path: Optional[str] = None) -> Dict[str, VolumeTrashInfo]:
    """Load the volume registry. Returns dict mapping volume_serial to info."""
    if registry_path is None:
        registry_path = get_registry_path()

    if not os.path.isfile(registry_path):
        return {}

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            serial: VolumeTrashInfo(
                volume_serial=serial,
                volume_name=entry.get("volume_name"),
                mount_point=entry.get("mount_point", ""),
                trash_path=entry.get("trash_path", ""),
                last_seen=entry.get("last_seen", ""),
                entry_count=entry.get("entry_count", 0),
                is_reachable=entry.get("is_reachable", True),
            )
            for serial, entry in data.items()
        }
    except (json.JSONDecodeError, OSError):
        return {}


def save_registry(
    registry: Dict[str, VolumeTrashInfo],
    registry_path: Optional[str] = None,
) -> None:
    """Save the volume registry atomically via dazzle_filekit.operations.atomic_write_json."""
    from dazzle_filekit.operations import atomic_write_json

    if registry_path is None:
        registry_path = get_registry_path()

    data = {
        serial: {
            "volume_name": info.volume_name,
            "mount_point": info.mount_point,
            "trash_path": info.trash_path,
            "last_seen": info.last_seen,
            "entry_count": info.entry_count,
            "is_reachable": info.is_reachable,
        }
        for serial, info in registry.items()
    }

    atomic_write_json(registry_path, data, indent=2)


def register_volume(
    volume: VolumeInfo, trash_path: str,
    registry_path: Optional[str] = None,
) -> None:
    """Register a per-volume trash store in the registry."""
    if not volume.volume_serial:
        return

    registry = load_registry(registry_path)
    registry[volume.volume_serial] = VolumeTrashInfo(
        volume_serial=volume.volume_serial,
        volume_name=volume.volume_name,
        mount_point=volume.mount_point,
        trash_path=trash_path,
        last_seen=_now_iso(),
        is_reachable=True,
    )
    save_registry(registry, registry_path)


def update_registry_reachability(
    registry_path: Optional[str] = None,
) -> Dict[str, VolumeTrashInfo]:
    """Scan all registered volumes and update reachability."""
    registry = load_registry(registry_path)

    for info in registry.values():
        info.is_reachable = os.path.isdir(info.trash_path)
        if info.is_reachable:
            info.last_seen = _now_iso()
            try:
                entries = [
                    d for d in os.listdir(info.trash_path)
                    if os.path.isdir(os.path.join(info.trash_path, d))
                ]
                info.entry_count = len(entries)
            except OSError:
                info.entry_count = 0

    save_registry(registry, registry_path)
    return registry


def get_all_trash_paths(
    central_path: str,
    registry_path: Optional[str] = None,
) -> List[str]:
    """Get all known trash store paths (central + per-volume) that exist."""
    paths = [central_path] if os.path.isdir(central_path) else []

    registry = load_registry(registry_path)
    for info in registry.values():
        if info.is_reachable and os.path.isdir(info.trash_path):
            if info.trash_path not in paths:
                paths.append(info.trash_path)

    return paths


# -- Resolve Trash Store for a Path --


def resolve_trash_store(
    source_path: str, central_path: str, create: bool = True
) -> Tuple[str, bool]:
    """Determine which trash store to use for a given source path.

    Tries per-volume trash first (for zero-copy rename). Falls back to
    central store if per-volume is not available.

    ``create=False`` decides without touching the disk: no store folder is
    made and the volume registry is not written. Planning uses it, so a
    preview changes nothing; ``commit_trash_store`` then does the creating
    for a real run.

    Returns:
        (trash_path, is_per_volume) tuple.
    """
    try:
        volume = get_volume_info(source_path)
    except OSError:
        return central_path, False

    per_volume_path = get_per_volume_trash_path(volume, create=create)
    if per_volume_path:
        try:
            if create:
                os.makedirs(per_volume_path, exist_ok=True)
            if is_same_volume(source_path, per_volume_path):
                if create:
                    register_volume(volume, per_volume_path)
                return per_volume_path, True
        except OSError:
            pass

    return central_path, False


def commit_trash_store(source_path: str, store_path: str, is_per_volume: bool
                       ) -> None:
    """Create a store chosen by ``resolve_trash_store(create=False)`` and,
    for a per-volume store, record it in the volume registry.

    ``source_path`` must still exist (it names the volume to register).
    """
    os.makedirs(store_path, exist_ok=True)
    if is_per_volume:
        try:
            register_volume(get_volume_info(source_path), store_path)
        except OSError:
            pass


def _now_iso() -> str:
    """Current time as ISO string."""
    import datetime
    return datetime.datetime.now().isoformat()
