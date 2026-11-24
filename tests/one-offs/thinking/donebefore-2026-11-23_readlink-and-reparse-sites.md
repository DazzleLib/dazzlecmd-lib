# Done-before pass: where else is "read a link / decide it is a link" done? (2026-11-23)

**Trigger:** a bug fix. `os.readlink` raises `ValueError` ("not a symbolic link") for a reparse point that is neither a symlink nor a junction: a WSL symlink (0xA000001D), an app execution alias (0x8000001B) or a cloud placeholder (0x9000xxxA). safedel's copy-first hit it (fixed 0.10.52); a read-only review by another session found the same fault in `core/links`. This pass asks where else the job is done, across dazzlecmd-lib, dazzlecmd (`github/src`) and dazzle-filekit, and in the review's report for dazzle-preservelib.

**The job, one level up:** "find out what a directory entry is, and what may be done to it, without following it or reading it." Other names for it: "read a link's target", "is this a link / junction?", "may I open this file?".

**The fault class, as one rule:** a site is safe when the guard that says "readable link" checks the **reparse tag** (or `S_ISLNK`), or when the catch covers `ValueError`. A site is unsafe when the guard checks only the **reparse bit** (0x400), or guesses "junction" when it cannot read the tag, and the catch is `OSError` only.

## 1. Every readlink

| Site | Guard | Catch | Verdict |
|---|---|---|---|
| dazzlecmd-lib `core/links/__init__.py` `get_link_target` | reparse bit (`is_linked_project`) | was `OSError` | **fixed** (0.10.54 candidate): catches `ValueError`, `NotImplementedError` |
| dazzlecmd-lib `core/links/_detect.py` `_safe_readlink` (used at :512, :526) | :512 `islink` (safe); :526 `_is_junction_win`, which guesses "junction" when it cannot open the entry or read its tag | was `OSError` | **fixed**, same catch |
| dazzlecmd-lib `core/safedel/_copyfirst.py` | any reparse bit | `OSError`, `NotImplementedError`, `ValueError` | safe (0.10.52) |
| dazzlecmd `claude-session-metadata` `get_symlink_target` | `Path.is_symlink()` (symlink tag only) | `OSError` | safe: the guard rules out other kinds |
| dazzle-filekit `links.py` `read_link_target` | `is_symlink()` then `is_junction()` | `OSError` | symlink branch safe; junction branch uses `read_junction_target`, not readlink, so not this fault; untested for a foreign kind |
| dazzle-filekit `operations.py` `copy_tree_preserving_links` (readlink at :348) | `is_junction()` at :322, which falls back to `not os.path.islink` (a guess of "junction") when it cannot read the tag | `OSError` | **unsafe, not reproduced**: a foreign reparse directory whose tag cannot be read is held back as a junction, then readlink raises `ValueError` out of the copy. Same shape as `_detect.py:526` |
| dazzle-preservelib `linkmirror/plan.py` `build_plan` (from the report) | reparse bit (`_is_link_st`) | `OSError` | **unsafe** (TEMPMESS read it; I read the same lines): a destination holding a foreign reparse kind crashes the plan. `verify.py` checks the tag first and is safe |

## 2. Where "is a link" is decided from the bit

| Site | What it does with the answer | Verdict |
|---|---|---|
| dazzlecmd-lib `is_linked_project` | "Linked to:" display; feeds `get_link_target` | imprecise (a OneDrive folder counts as linked) but harmless now that the readlink is safe |
| dazzlecmd-lib safedel `_platform.py:370`, `_recover.py:122`, `_store.py:1114` | refuse to rmtree a reparse point (rmdir instead); don't descend when sizing; list leftovers instead of removing | safe: each fails closed (a refusal or an undercount, never a read or a delete through) |
| dazzlecmd `_repo_common/private_state.py` `_is_reparse` | judges whether `private/` is linked | imprecise, low risk (judgement only) |
| dazzlecmd `unlockit/holders.py`, `safe-icacls` | skip reparse points while walking | safe: skipping is the cautious direction |

## 3. Reading contents of something that may be online-only

- safedel copy-first: a top-level item is checked against the link catalogue (0.10.53); inside a folder every reparse point is recorded and not copied. Safe.
- **Not known, and the most important open row:** what OneDrive does when safedel *renames* an online-only file or folder out of the OneDrive folder into the trash (same volume). The cloud filter may download it first, or OneDrive may record a cloud-side delete. Nothing here has observed it. Settled only by one real test in a scratch folder inside OneDrive, with the maintainer's go.

## 4. Following a link where lstat was meant

- safedel `_platform._discard_partial_copy` and `_recover._move_back` both decide "copy/remove as a folder" with `isdir(...) and not islink(...)`, which is True for a junction. Measured: a lone junction staged and recovered across drives (rename forced to `EXDEV`) comes back as a link with its target intact, because safedel never sends a junction down that path. Pinned by the new guard test `test_a_lone_directory_link_staged_and_recovered_across_drives_stays_a_link`. On Python 3.9+ `shutil.rmtree` also refuses a top-level junction rather than entering it.
- The other `isdir`/`stat` calls in safedel's core either act on the batch's own folders or deliberately ask about a link's target. Read, not tested one by one.

## Outcome

**Unify, later; same rule, now.** The right end state is one tag-classifying "safe to read the target?" helper used by every site; safedel's `_linkkinds` already encodes it (`target_readable`). It lives in safedel, and `core/links` is the lower layer, so the move is part of the link-catalogue design's library step, not a cross-import today. Until then every readlink site catches `ValueError` and `NotImplementedError` with `OSError`.

**On the axis** "how the site learns what kind of entry it holds", the safe sites read the tag (or `S_ISLNK`) and the unsafe ones read only the bit or guess, because the bit is shared by every reparse kind and only the tag tells them apart.

**Tests carried across:** the synthetic pattern from `test_core_safedel_linkkinds.py` (patch the stat or `os.readlink`; no real WSL, alias or cloud file) is in `tests/test_core_links_foreign_reparse.py` (5 tests; 4 red on the old code, green after). The filekit and preservelib sites need the same tests in their repositories.
