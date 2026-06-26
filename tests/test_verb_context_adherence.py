"""AC-T3: the consumer's concrete contexts adhere to the bedrock VerbContext
capability contract (context-contract DWP, T2-2).

`VerbContext` (apply/undo over an opaque entity) is the named bedrock contract;
"bedrock declares, consumer adheres" is now true of behavior, not just the value
and identity contracts. `issubclass` works because `VerbContext` is a method-only
`@runtime_checkable` Protocol -- no instance construction needed.
"""
from dazzle_lib import VerbContext

from dazzlecmd_lib.contexts import (
    ActivationContext,
    AliasRebindContext,
    ContainmentContext,
    KitMembershipContext,
    ProjectionContext,
    VisibilityContext,
)


def test_concrete_contexts_adhere_to_verb_context():   # AC-T3
    for ctx in (AliasRebindContext, ProjectionContext, VisibilityContext,
                ContainmentContext, KitMembershipContext):
        assert issubclass(ctx, VerbContext), ctx.__name__


def test_activation_context_is_apply_only():
    # ActivationContext intentionally exposes apply without undo (enable<->disable
    # is its own inverse via a fresh apply), so it is NOT a full VerbContext -- a
    # truthful, deliberate exception, not a regression.
    assert not issubclass(ActivationContext, VerbContext)
