"""Which links point at this folder? The target-side defence for safedel.

A junction or directory symlink is safe to delete: only the link goes. The
dangerous direction is the other one -- a command is handed the link's
TARGET (because something resolved the path, or because the caller meant the
link and typed the real folder) and deletes the real data that the link was
only a view of. On 2026-11-17 that is how two ``private/`` repositories were
emptied: each was the destination of a junction inside a git worktree.

``links_pointing_at(path)`` answers, for one real directory, "which known
links have this exact folder as their destination?". "Known" means:

- a registry file, when one is given (``junctions.json``: a list of
  ``{"link": ..., "target": ...}`` records, written by the tools that create
  junctions), and
- the worktrees of EVERY git repository that encloses the folder (the
  folder may itself be a repository nested in another): each worktree
  root's direct children that are junctions or directory symlinks. The
  worktree lists are read from ``<repo>/.git/worktrees/*/gitdir`` -- plain
  files; no git process is started.

Comparison is lexical (``normcase(abspath(...))``). Nothing here resolves a
path: resolving is what follows a link to its target, and following links is
the failure this module exists to prevent.

The match is exact. Deleting something INSIDE a junction's destination is
ordinary work and is not this module's business.
"""

import json
import os
from typing import List, NamedTuple, Optional

from dazzlecmd_lib.core.links import detect_link, LINK_JUNCTION, LINK_SYMLINK


class KnownLink(NamedTuple):
    link_path: str
    link_type: str      # "junction" | "symlink"
    source: str         # "registry" | "worktree"


def _key(path: str) -> str:
    """Lexical comparison key. Never resolves."""
    p = str(path)
    if p.startswith("\\\\?\\"):
        p = p[4:]
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/")


def _enclosing_repos(path: str) -> List[str]:
    """EVERY ancestor of ``path`` (itself included) holding a ``.git``
    directory, nearest first.

    Every one, not the nearest: the folder a worktree's junction points at is
    often itself a repository nested inside the main one (a project's
    ``private/`` notes repo), and the worktrees belong to the OUTER
    repository. Stopping at the nearest ``.git`` reads the inner repo's empty
    worktree list and sees nothing -- which is the exact shape of the
    2026-11-17 incident. A ``.git`` FILE marks a linked worktree or
    submodule, which has no worktree list of its own; the walk passes it.
    """
    repos = []
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            repos.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            return repos
        current = parent


def _worktree_roots(repo: str) -> List[str]:
    """Roots of the repository's linked worktrees, from its own bookkeeping."""
    roots = []
    wt_dir = os.path.join(repo, ".git", "worktrees")
    try:
        names = os.listdir(wt_dir)
    except OSError:
        return roots
    for name in names:
        entry_dir = os.path.join(wt_dir, name)
        gitdir_file = os.path.join(entry_dir, "gitdir")
        try:
            with open(gitdir_file, "r", encoding="utf-8", errors="replace") as fh:
                gitdir = fh.read().strip()
        except OSError:
            continue
        if not gitdir:
            continue
        gitdir = gitdir.replace("/", os.sep)
        if not os.path.isabs(gitdir):
            # git's worktree.useRelativePaths (2.48+): relative to this entry
            # folder. Joined and normalised lexically -- never resolved.
            gitdir = os.path.normpath(os.path.join(entry_dir, gitdir))
        # gitdir is "<worktree root>/.git"
        root = os.path.dirname(gitdir)
        if root and os.path.isdir(root):
            roots.append(root)
    return roots


def _links_in(folder: str) -> List[tuple]:
    """(path, type, target) for each direct child of ``folder`` that is a
    junction or a directory symlink."""
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        child = os.path.join(folder, name)
        try:
            info = detect_link(child)
        except Exception:
            continue
        if info is None or not info.target:
            continue
        if info.link_type == LINK_JUNCTION:
            found.append((child, "junction", info.target))
        elif info.link_type == LINK_SYMLINK and info.is_dir:
            found.append((child, "symlink", info.target))
    return found


def _registry_links(registry_path: Optional[str]) -> List[tuple]:
    """(path, type, target) records from the registry file, if readable."""
    if not registry_path or not os.path.isfile(registry_path):
        return []
    try:
        with open(registry_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    records = data.get("links", []) if isinstance(data, dict) else data
    found = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        link, target = rec.get("link"), rec.get("target")
        if link and target:
            found.append((link, rec.get("type", "junction"), target))
    return found


def links_pointing_at(path: str, registry_path: Optional[str] = None
                      ) -> List[KnownLink]:
    """Known junctions and directory symlinks whose destination is exactly
    ``path``. A registry record counts only while its link still exists and
    still points here (a stale record must not refuse a legitimate delete).
    """
    want = _key(path)
    hits: List[KnownLink] = []
    seen = set()

    for link, _type, _target in _registry_links(registry_path):
        try:
            info = detect_link(link)
        except Exception:
            info = None
        if info is None or not info.target:
            continue
        if _key(info.target) != want:
            continue
        kind = "junction" if info.link_type == LINK_JUNCTION else "symlink"
        k = _key(link)
        if k not in seen:
            seen.add(k)
            hits.append(KnownLink(link, kind, "registry"))

    for repo in _enclosing_repos(path):
        for root in _worktree_roots(repo):
            for link, kind, target in _links_in(root):
                if _key(target) != want:
                    continue
                k = _key(link)
                if k not in seen:
                    seen.add(k)
                    hits.append(KnownLink(link, kind, "worktree"))
    return hits
