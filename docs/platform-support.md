# Platform Support

`dazzlecmd-lib` is pure Python and targets all major platforms. The mode subsystem and the `core.safedel` trash engine have the most platform-specific surface (junctions/symlinks, NTFS ADS, ACLs, volume routing).

| Platform | Status | Notes |
|----------|--------|-------|
| Windows 10/11 | ✅ Tested | Primary development platform. Junction/symlink handling, NTFS ADS, drive-type volume routing (via `unctools`). |
| Linux | ✅ Tested | CI matrix. |
| macOS | ✅ Tested | CI matrix. |
| BSD | 🟡 Expected | Pure-Python paths expected to work; not in CI. |

## Python versions

Tested on CPython **3.9 – 3.13** (CI matrix on Linux + macOS).

## Optional / platform-gated dependencies

- `unctools` — Windows-only (`sys_platform == 'win32'`); POSIX uses in-module stubs.
- `colorama` (the `color` extra) — Windows-only; modern Windows handles ANSI natively, only legacy `cmd.exe` needs it.
- `dazzle-filekit` — cross-platform metadata preservation used by `core.safedel`.
