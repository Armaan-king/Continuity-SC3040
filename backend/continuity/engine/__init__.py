"""Continuity's constraint engine — pure Python, zero LLM, zero network.

The engine decides what is broken. The model decides what to do about it. The engine
then re-checks the model's work. Nothing in this package imports an SDK or a client;
that is the property the whole pitch rests on, so it is worth keeping structurally
true rather than merely intended.
"""

from .models import (
    Alternative,
    Board,
    Edge,
    Evidence,
    PartSpec,
    Rail,
    Repair,
    Requirements,
    Slot,
    Verdict,
)
from .policy import MAX_REPAIRS, Guarded, Resolution, enforce, legal_set, plan_resolution
from .rules import RULES, evaluate, failures, for_subject, passing

__all__ = [
    "Alternative",
    "Board",
    "Edge",
    "Evidence",
    "Guarded",
    "MAX_REPAIRS",
    "PartSpec",
    "Rail",
    "Repair",
    "Requirements",
    "Resolution",
    "RULES",
    "Slot",
    "Verdict",
    "enforce",
    "evaluate",
    "failures",
    "for_subject",
    "legal_set",
    "passing",
    "plan_resolution",
]
