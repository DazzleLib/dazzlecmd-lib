# Mutation survivors: equivalent and don't-care verdicts

Guarded memory for `/test-mutation`. Entries are grouped under the target file with its `git hash-object` prefix; when the file's current hash no longer matches, every entry under it is stale and must be re-triaged before reuse. Only separated-generation runs (modes 1-2) write here.

## dazzlecmd_lib/core/safedel/_platform.py @ 36c6bf1dbd59

- **`if not os.path.lexists(dest_path):` -> `if not os.path.exists(dest_path):`** (in `_discard_partial_copy`) -- **equivalent**. The two differ only when `dest_path` is a dangling symlink. `_discard_partial_copy` is called only after `_stage_regular` copied a regular file (`shutil.copy2`, which writes a regular file) or a regular directory (`copy_tree_preserving_links`, whose root is a directory), so a dangling link at the destination root is unreachable. 2026-11-09, mode 1 (fresh-context generator), sweep `v0.10.36__report__safedel-hotfix.json` M08.
- **`if os.path.isdir(dest_path) and not os.path.islink(dest_path):` -> `if os.path.isdir(dest_path):`** (in `_discard_partial_copy`) -- **equivalent**. Differs only when `dest_path` is a symlink to a directory, which the same copy calls never create at the destination root. Even if reached, `shutil.rmtree` refuses a symlink (raises `OSError`), which the function catches and reports. 2026-11-09, mode 1, same sweep, M09.
