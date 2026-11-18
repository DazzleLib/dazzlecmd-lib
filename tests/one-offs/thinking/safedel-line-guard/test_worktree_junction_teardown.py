"""Does `git worktree remove` delete THROUGH a private/ junction into its target?

Builds dummy repos entirely under SAFE_ROOT (this script's own folder) and
refuses to touch anything outside it. Three cases:
  A  private/ not ignored by the main repo; worktree remove WITHOUT --force
  B  same, WITH --force
  C  private/ listed in .gitignore (as in the real repos); remove WITHOUT --force
For each: the junction target's file count before and after, and git's output.
"""
import os
import subprocess
import sys
from pathlib import Path

SAFE_ROOT = Path(__file__).resolve().parent / "wt-junction-test"


def safe(p: Path) -> Path:
    p = Path(p).resolve()
    if SAFE_ROOT.resolve() not in (p, *p.parents):
        sys.exit(f"REFUSING: {p} is outside {SAFE_ROOT}")
    return p


def git(repo: Path, *args):
    r = subprocess.run(
        ["git", "-C", str(safe(repo)), "-c", "commit.gpgsign=false",
         "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def junction(link: Path, target: Path):
    safe(link); safe(target)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
                   check=True)


def count(d: Path) -> int:
    return sum(1 for _ in safe(d).rglob("*") if _.is_file()) if d.exists() else -1


def case(name: str, ignore_private: bool, force: bool):
    root = safe(SAFE_ROOT / name)
    main, wt = root / "main", root / "wt"
    (main / "private" / "claude").mkdir(parents=True)
    for i in range(3):
        (main / "private" / "claude" / f"note{i}.md").write_text(f"note {i}\n")
    git(main, "init", "-q", "-b", "main")
    (main / "README.md").write_text("x\n")
    if ignore_private:
        (main / ".gitignore").write_text("private/\n")
    git(main, "add", "README.md", *([".gitignore"] if ignore_private else []))
    git(main, "commit", "-q", "-m", "init")
    rc, out = git(main, "worktree", "add", "-q", "-b", "feat", str(wt), "main")
    assert rc == 0, out
    junction(wt / "private", main / "private")
    before = count(main / "private")
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(wt)]
    rc, out = git(main, *args)
    after = count(main / "private")
    print(f"{name}: ignore_private={ignore_private} force={force} "
          f"target files before={before} after={after} git_rc={rc}")
    if out:
        print("   git:", out.replace("\n", "\n        "))
    print("   worktree dir still exists:", wt.exists(),
          "| junction still exists:", (wt / "private").exists())


if __name__ == "__main__":
    if SAFE_ROOT.exists():
        sys.exit(f"{SAFE_ROOT} already exists; pick a fresh folder")
    SAFE_ROOT.mkdir()
    print(subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip())
    case("A-not-ignored-noforce", ignore_private=False, force=False)
    case("B-not-ignored-force", ignore_private=False, force=True)
    case("C-ignored-noforce", ignore_private=True, force=False)
