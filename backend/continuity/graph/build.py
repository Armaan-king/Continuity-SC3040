"""Graph assembly. Mirrors design doc §8.

```
parse_requirements ─(unknown supply)─▶ clarify [interrupt] ─┐
        │◄──────────────────────────────────────────────────┘
      plan ──▶ select ──▶ validate ──┬─(pass, more slots)──▶ select
                  ▲                  ├─(pass, done)────────▶ finalize
                  │                  └─(conflict)──▶ review ─┬─▶ apply ─▶ validate
                  └──────────────────────────────────────────┴─▶ escalate [interrupt] ─▶ replan ─▶ validate
```

`validate` routes electrical results from deterministic verdicts. Empty searches and
exhausted automatic repairs pause at `escalate`. Explicit user choices enter `recover`,
which offers candidates, replaces a selection, or replans before returning to validation.
Each manual attempt is bounded; another attempt requires another user decision.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes, recovery
from .state import DesignState


def build(checkpointer=None):
    graph = StateGraph(DesignState)

    graph.add_node("parse_requirements", nodes.parse_requirements)
    graph.add_node("clarify", nodes.clarify)
    graph.add_node("plan", nodes.plan)
    graph.add_node("replan", nodes.replan)
    graph.add_node("select", nodes.select)
    graph.add_node("validate", nodes.validate)
    graph.add_node("review", nodes.review)
    graph.add_node("apply", nodes.apply)
    graph.add_node("escalate", nodes.escalate)
    graph.add_node("finalize", nodes.finalize)
    graph.add_node("recover", recovery.recover)

    graph.add_edge(START, "parse_requirements")
    graph.add_conditional_edges(
        "parse_requirements",
        nodes.needs_clarification,
        {"clarify": "clarify", "plan": "plan"},
    )
    # Back to itself when the answer matched nothing. `clarify` has no default, so an
    # unrecognised supply stays unresolved rather than becoming a silent 5 V board — and
    # this loop cannot spin, because every pass blocks on a fresh human answer.
    graph.add_conditional_edges(
        "clarify",
        nodes.needs_clarification,
        {"clarify": "clarify", "plan": "plan"},
    )
    graph.add_edge("plan", "select")
    graph.add_conditional_edges("select", lambda state: "escalate" if state.get("escalation") else "validate",
                                {"escalate": "escalate", "validate": "validate"})
    graph.add_conditional_edges(
        "validate",
        nodes.after_validate,
        {"review": "review", "select": "select", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "review", nodes.after_review, {"apply": "apply", "escalate": "escalate"}
    )
    # `apply` can run out of candidates, which is an escalation, not a retry. Routing
    # it unconditionally back to `validate` would re-enter the loop on a board nothing
    # can fix and let the run end without ever telling the user why.
    graph.add_conditional_edges(
        "apply", nodes.after_apply,
        {"select": "select", "validate": "validate", "escalate": "escalate"},
    )
    # An escalation is a pause, not an ending: an accepted fault carries on with the
    # rest of the board. Routing it straight to finalize abandoned every unplaced slot.
    graph.add_conditional_edges(
        "escalate",
        nodes.after_escalate,
        {"replan": "replan", "validate": "validate", "finalize": "finalize", "recover": "recover"},
    )
    graph.add_edge("replan", "validate")
    graph.add_conditional_edges("recover", recovery.after_recovery,
                               {"select": "select", "validate": "validate", "escalate": "escalate"})
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())
