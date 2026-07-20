"""Tests for dazzlecmd_lib.verb_contracts -- the contract continuum (#104).

Pure string logic: the pre-argparse `--` split for verb-mediated verbs
and the host-correct shell join for forwarded level-args. The
subprocess-level test that runs the documented command verbatim
(`dz setup <tool> -- --dry-run`) lives with the app's integration
tests / human checklist -- this file proves the mechanism.
"""

import os

import pytest

from dazzlecmd_lib.verb_contracts import (
    CONTRACT_BARE,
    CONTRACT_VERB_MEDIATED,
    VERB_CONTRACTS,
    contract_for,
    join_for_shell,
    split_level_args,
)


class TestSubscriptions:
    def test_setup_subscribes_verb_mediated(self):
        assert contract_for("setup") == CONTRACT_VERB_MEDIATED

    def test_unsubscribed_verbs_are_bare(self):
        assert contract_for("list") == CONTRACT_BARE
        assert contract_for("info") == CONTRACT_BARE
        assert contract_for("") == CONTRACT_BARE

    def test_registry_values_are_known_variants(self):
        for verb, variant in VERB_CONTRACTS.items():
            assert variant in (CONTRACT_VERB_MEDIATED,), (
                f"{verb} subscribes to an unimplemented variant {variant}")


class TestSplitLevelArgs:
    def test_documented_form_splits(self):
        # The v0.7.46 documented command, verbatim shape.
        head, level = split_level_args(
            ["setup", "mytool", "--", "--dry-run"])
        assert head == ["setup", "mytool"]
        assert level == ["--dry-run"]

    def test_verb_params_stay_in_head(self):
        head, level = split_level_args(
            ["setup", "dz", "--yes", "--", "--force", "x y"])
        assert head == ["setup", "dz", "--yes"]
        assert level == ["--force", "x y"]

    def test_no_separator_no_split(self):
        head, level = split_level_args(["setup", "mytool", "--yes"])
        assert head == ["setup", "mytool", "--yes"]
        assert level == []

    def test_only_first_separator_splits(self):
        head, level = split_level_args(
            ["setup", "t", "--", "a", "--", "b"])
        assert head == ["setup", "t"]
        assert level == ["a", "--", "b"]

    def test_bare_verbs_untouched(self):
        argv = ["list", "--", "whatever"]
        head, level = split_level_args(argv)
        assert head == argv
        assert level == []

    def test_empty_argv(self):
        assert split_level_args([]) == ([], [])

    def test_empty_tail(self):
        head, level = split_level_args(["setup", "t", "--"])
        assert head == ["setup", "t"]
        assert level == []

    def test_input_not_mutated(self):
        argv = ["setup", "t", "--", "x"]
        split_level_args(argv)
        assert argv == ["setup", "t", "--", "x"]


class TestJoinForShell:
    def test_empty(self):
        assert join_for_shell([]) == ""

    def test_plain_args(self):
        assert join_for_shell(["--dry-run", "-n"]) == "--dry-run -n"

    def test_arg_with_spaces_is_quoted(self):
        joined = join_for_shell(["--path", "C:\\spaced dir\\x"
                                 if os.name == "nt" else "/spaced dir/x"])
        # Host-correct quoting: the spaced arg survives as ONE token.
        assert "spaced dir" in joined
        assert '"' in joined or "'" in joined

    @pytest.mark.skipif(os.name == "nt", reason="POSIX quoting")
    def test_posix_quote_safety(self):
        assert join_for_shell(["a;b"]) == "'a;b'"
