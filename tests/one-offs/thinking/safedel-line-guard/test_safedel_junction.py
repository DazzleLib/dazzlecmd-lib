"""Does `dz safedel --yes` remove a junction without touching its target?

Case 1: safedel the junction itself.
Case 2: safedel a folder that CONTAINS a junction (shaped like a worktree
        with private/ -> main/private), as a replacement for
        `git worktree remove`.
Everything lives under SAFE_ROOT (beside this script); the script refuses
any path outside it. Each step's result is printed, not assumed.
"""
import os
import subprocess
import sys
from pathlib import Path

SAFE_ROOT = Path(os.path.abspath(Path(__file__).parent / "safedel-junction-test3"))


def safe(p):
    # abspath, NOT resolve(): resolve() follows junctions, which handed the
    # first run's safedel the junction's TARGET instead of the junction.
    p = Path(os.path.abspath(p))
    if SAFE_ROOT not in (p, *p.parents):
        sys.exit(f"REFUSING: {p} outside {SAFE_ROOT}")
    return p


def junction(link, target):
    safe(link); safe(target)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
                   check=True)


def files(d):
    return sorted(p.relative_to(d).as_posix() for p in safe(d).rglob("*") if p.is_file())


def is_junction(p):
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(Get-Item -Force '{p}').LinkType"], capture_output=True, text=True)
    return r.stdout.strip()


def safedel(path):
    r = subprocess.run(["dz", "safedel", "--yes", str(safe(path))],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-3:]
    return r.returncode, tail


if SAFE_ROOT.exists():
    sys.exit(f"{SAFE_ROOT} exists; use a fresh folder")
target = safe(SAFE_ROOT / "main" / "private")
(target / "claude").mkdir(parents=True)
for i in range(3):
    (target / "claude" / f"note{i}.md").write_text(f"note {i}\n")
before = files(target)

# Case 1: the junction itself
j1 = SAFE_ROOT / "j1"
junction(j1, target)
print("case 1: j1 LinkType =", is_junction(j1))
rc, tail = safedel(j1)
print(f"case 1: safedel rc={rc}", tail)
print("case 1: junction gone:", not j1.exists(), "| target intact:", files(target) == before)

# Case 2: a folder containing a junction
wt = safe(SAFE_ROOT / "wt")
(wt / "src").mkdir(parents=True)
(wt / "src" / "code.py").write_text("x = 1\n")
junction(wt / "private", target)
print("case 2: wt/private LinkType =", is_junction(wt / "private"))
rc, tail = safedel(wt)
print(f"case 2: safedel rc={rc}", tail)
print("case 2: worktree folder gone:", not wt.exists(), "| target intact:", files(target) == before)
print("target files now:", files(target))
