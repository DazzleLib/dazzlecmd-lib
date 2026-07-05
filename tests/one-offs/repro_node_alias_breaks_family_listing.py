"""FIXED 2026-07-05 (lib 0.10.16-alpha): cmd_list now passes
apply_value_alias=False to _canonical -- a family listing is about the
NODE's children, never the node's VALUE. Living regression:
test_prop_commands.py::TestTesterHoldFixes. Kept as history."""
"""Repro: `dz :.level:.` (family/plane listing) reports empty even when
rung properties genuinely exist under `dz:.level:*`.

Found while running the v0.8.1 bedrock checklist, Section 1.6
(`tests/checklists/v0.8.1__Feature__nucleus-rank-addressing-and-level-alias.md`
in dazzle-lib). The checklist framed this as a UX-comprehensibility
question ("does the listing distinguish the alias from the family?") but
the actual behavior is a functional bug: the listing ALWAYS reports empty,
regardless of how many rungs are set.

Root cause (dazzlecmd_lib/prop_commands.py):

  `cmd_list()` calls the same `_canonical(engine, path_text)` helper used
  by `cmd_get`/`cmd_set`/`cmd_delete`. `_canonical` unconditionally applies
  `NODE_VALUE_ALIASES` when the canonicalized text exactly matches a
  registered alias key (e.g. "tst:.level" -> "tst.level"). That's correct
  for get/set/delete (the node's bare VALUE really does live at the
  flattened dotted key) but wrong for LIST: `dz :.level:.` should enumerate
  the FIBER children stored under the colon-spelled prefix
  ("tst:.level:kit", "tst:.level:tool", ...), not query the alias TARGET
  prefix ("tst.level"), where nothing is ever stored.

  Net effect: `list_prefix("tst.level")` is queried instead of
  `list_prefix("tst:.level")` -- a prefix under which nothing can ever
  exist (rungs are colon-keyed) -- so the listing silently reports "no
  properties set" forever, even with rungs present.

Run: python tests/one-offs/repro_node_alias_breaks_family_listing.py
"""
import sys
import tempfile

from dazzlecmd_lib.engine import AggregatorEngine
from dazzlecmd_lib import prop_commands


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        e = AggregatorEngine(name="t", command="tst", config_dir=tmp)
        prop_commands.register_node_value_alias("tst:.level", "tst.level")

        # Set two rung properties under the level fiber -- exactly like
        # `dz :.level:kit=x` / `dz :.level:tool=y` in the real CLI.
        e.property_store.set("tst:.level:kit", "x")
        e.property_store.set("tst:.level:tool", "y")

        # Sanity: the rungs really are there (matches `dz prop list`).
        direct = e.property_store.list_prefix("tst:.level")
        print(f"direct list_prefix('tst:.level') = {direct}")
        assert direct == {"tst:.level:kit": "x", "tst:.level:tool": "y"}, (
            "setup invariant broken -- rungs did not land as expected"
        )

        # The bug: the family-listing command path (`:.level:.` sugar)
        # goes through cmd_list(), which reroutes via the node-value
        # alias and queries the WRONG prefix.
        code = prop_commands.cmd_list(e, "tst:.level")
        print(f"cmd_list(engine, 'tst:.level') exit={code}")

        if direct and code == 0:
            print(
                "BUG CONFIRMED: cmd_list reported no properties even "
                "though list_prefix('tst:.level') shows 2 entries.",
                file=sys.stderr,
            )
            return 1
        print("not reproduced (listing found the rungs) -- bug may be fixed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
