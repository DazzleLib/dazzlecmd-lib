"""Does `git push --force-with-lease` refuse when the remote moved? (D5 of the guard)

Builds everything under the folder given as argv[1] (it must not exist yet):
a bare "remote", clone A and clone B. B pushes a new commit; then A, which has
rewritten its history, force-pushes in four arms:

  1. --force-with-lease, A has NOT fetched since B pushed
  2. --force-with-lease, A HAS fetched (no merge)       <- the documented hole
  3. --force-with-lease --force-if-includes, A has fetched (no merge)
  4. --force-with-lease --force-if-includes, A has not fetched

Every git command carries -C <scratch path> and per-invocation identity and
no signing, so nothing can land in a real repository or prompt for a key.
The "remote" is a local bare repository in the scratch folder; nothing is
pushed anywhere else. Prints each arm's exit code and whether B's commit
survived on the remote.
"""
import os
import subprocess
import sys

ID = ["-c", "user.email=probe@example.invalid", "-c", "user.name=probe",
      "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
      "-c", "init.defaultBranch=main"]


def git(where, *args, check=True):
    r = subprocess.run(["git", "-C", where] + ID + list(args),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit("git %s failed in %s:\n%s" % (" ".join(args), where, r.stderr))
    return r


def commit(where, name, text):
    with open(os.path.join(where, name), "w") as fh:
        fh.write(text)
    git(where, "add", name)
    git(where, "commit", "-q", "-m", text)


def arm(root, n, fetch, flags):
    base = os.path.join(root, "arm%d" % n)
    remote, a, b = (os.path.join(base, x) for x in ("remote.git", "a", "b"))
    os.makedirs(base)
    subprocess.run(["git"] + ID + ["init", "-q", "--bare", remote], check=True)
    subprocess.run(["git"] + ID + ["clone", "-q", remote, a], check=True, capture_output=True)
    commit(a, "f.txt", "one")
    git(a, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git"] + ID + ["clone", "-q", remote, b], check=True, capture_output=True)
    commit(b, "g.txt", "B's work")
    git(b, "push", "-q", "origin", "HEAD:main")
    commit(a, "f.txt", "two")
    git(a, "commit", "-q", "--amend", "-m", "two, rewritten")
    if fetch:
        git(a, "fetch", "-q", "origin")
    r = git(a, "push", "-q", *flags, "origin", "HEAD:main", check=False)
    log = git(remote, "log", "--format=%s", "main").stdout.split("\n")
    survived = "B's work" in log
    print("arm %d  fetched=%-5s flags=%-42s exit=%d  B's commit on the remote: %s"
          % (n, fetch, " ".join(flags), r.returncode, "kept" if survived else "LOST"))
    return r.returncode, survived


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = argv[1]
    if os.path.exists(root):
        raise SystemExit("refusing: %s already exists" % root)
    os.makedirs(root)
    print(subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip())
    arm(root, 1, False, ["--force-with-lease"])
    arm(root, 2, True, ["--force-with-lease"])
    arm(root, 3, True, ["--force-with-lease", "--force-if-includes"])
    arm(root, 4, False, ["--force-with-lease", "--force-if-includes"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
