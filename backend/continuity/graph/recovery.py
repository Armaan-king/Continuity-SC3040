"""Explicit, repeatable user recovery; each action does bounded work then revalidates."""
from __future__ import annotations

from dataclasses import replace

from .. import llm
from ..engine import policy, rules
from ..engine.controllers import intended_controllers, missing_controller
from ..engine.models import Slot
from ..planner import plan as planner, revision, topology
from ..parts.mcp import ToolError
from . import sourcing

SEARCH = "Search for more components"
REDESIGN = "Redesign the device"
CONTROLLER = "Add or replace the controller"
WEB = "Research alternatives with Exa"
CHOOSE = "Choose a different component"


def board_of(state):
    return topology.Board(state["requirements"], state["slots"], state["rails"])


def target_of(state):
    if state.get("recovery_target") in state["slots"]:
        return state["recovery_target"]
    failures = rules.failures(state.get("verdicts", []))
    return failures[0].subject if failures else state.get("current")


def needs_controller(state):
    failures = rules.failures(state.get("verdicts", []))
    return bool(failures and missing_controller(failures[0], board_of(state)))


def options(state):
    choices = [CONTROLLER] if needs_controller(state) and not state.get("recovery_target") else [SEARCH]
    return choices + [CHOOSE, WEB, REDESIGN]


def current_plan(state):
    old = state["plan"]
    return planner.Plan(state["requirements"], state["slots"],
                        [r for key, r in state["rails"].items() if key != topology.INPUT_RAIL_ID],
                        dict(getattr(old, "queries", {})), tuple(getattr(old, "links", ())),
                        planner.placement_order(state["slots"]))


def publish(state, plan, config, *, replacements=frozenset()):
    from . import nodes
    slots = {}
    reused = []
    for key, proposed in plan.slots.items():
        old = state["slots"].get(key)
        same_kind = old and (old.constraint or {}).get("category") == (proposed.constraint or {}).get("category")
        wrong_controller = ((proposed.constraint or {}).get("category") == "mcu"
                            and old and old.part and old.part.role != "master")
        if old and old.part and same_kind and key not in replacements and not wrong_controller:
            slots[key] = replace(proposed, part=old.part, status="pending", pinned=old.pinned or proposed.pinned,
                                 repair_count=old.repair_count, quantity=old.quantity)
            reused.append(key)
        else:
            slots[key] = replace(proposed, part=None, status="pending")
    plan = replace(plan, slots=slots)
    rails = topology.assemble_rails(plan.rails, plan.requirements, slot_ids=tuple(slots))
    pending = [key for key in plan.order if slots[key].part is None]
    thread = nodes._thread_id(config)
    if thread:
        nodes.clear_prefetches(thread, cancel=True)
    ev = nodes._events(config)
    nodes._emit(ev.plan(slots.values(), topology.power_edges(rails) + topology.unmodelled(slots, rails)
                       + topology.planned_data_edges(plan.links),
                       topology.supply_node(topology.power_source(plan.requirements)), replace_board=True))
    nodes._emit(ev.reasoning(None, f"Reconfigured the board: kept {len(reused)} selected component(s); "
                            f"{len(pending)} slot(s) need sourcing. All checks will run again."))
    return {"plan": plan, "requirements": plan.requirements, "slots": slots, "rails": rails,
            "pending": pending, "current": None, "verdicts": [], "accepted": [],
            "escalation": None, "guidance": None, "reconfiguration": None, "recovery_action": None,
            "recovery_target": None,
            "offered_alternatives": [], "alternative_slot": None, "repair_action": None,
            "constraint": None, "unfilled": [], "revalidate_all": True, "manual_choice": False,
            "candidates": {key: value for key, value in state.get("candidates", {}).items() if key in reused},
            "cursor": {key: value for key, value in state.get("cursor", {}).items() if key in reused}}


def public_alternatives(state):
    return [{key: row[key] for key in ("action", "label", "summary", "source", "verification") if key in row}
            for row in state.get("offered_alternatives", [])]


def catalogue_offer(candidate, index):
    return {"action": f"Try alternative {index}: {candidate.mpn}",
            "label": f"{candidate.mpn} · {candidate.manufacturer}",
            "summary": candidate.description[:300], "source": candidate.product_url,
            "verification": "Catalogue candidate; not yet checked on this board", "candidate": candidate}


def known_alternatives(state, key):
    """Previously placed parts are cheap to restore, but their old verdicts are not reused."""
    current = state["slots"][key].part
    seen = {_part_key(current)} if current else set()
    rows = []
    for part in reversed(state.get("replacement_history", {}).get(key, [])):
        if _part_key(part) in seen:
            continue
        seen.add(_part_key(part))
        rows.append({"action": f"Try alternative {len(rows) + 1}: {part.mpn}", "label": f"{part.mpn} · Previous selection",
                     "summary": part.description[:300], "source": part.product_url or part.datasheet,
                     "verification": "Previous selection; all checks will run again", "part": part})
    for row in state.get("researched_alternatives", {}).get(key, []):
        identity = (row["manufacturer"].casefold(), row["mpn"].casefold())
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({**row, "action": f"Try alternative {len(rows) + 1}: {row['mpn']}"})
    for candidate in state.get("candidates", {}).get(key, []):
        if _part_key(candidate) in seen:
            continue
        seen.add(_part_key(candidate))
        rows.append(catalogue_offer(candidate, len(rows) + 1))
    return rows[:6]


def _part_key(part):
    return (part.manufacturer.casefold(), part.mpn.casefold())


async def recover(state, config):
    from . import nodes
    ev = nodes._events(config)
    action = state.get("recovery_action")
    key = state.get("alternative_slot") if action and action.startswith("Try alternative ") else target_of(state)
    base = {"recovery_action": None, "reconfiguration": None, "guidance": None}
    try:
        if action == CHOOSE:
            offered = [{"action": f"Change component: {k}", "slot": k, "label": s.label,
                        "summary": s.part.mpn if s.part else "No component selected",
                        "verification": "Choose this slot to see alternatives or search again"}
                       for k, s in state["slots"].items()]
            return {**base, "offered_alternatives": offered, "alternative_slot": None,
                    "escalation": "Which component should change? The other selections will be preserved and the whole board rechecked."}
        chosen_slot = next((row.get("slot") for row in state.get("offered_alternatives", [])
                            if row["action"] == action and row.get("slot") in state["slots"]), None)
        if chosen_slot:
            offered = known_alternatives(state, chosen_slot)
            return {**base, "recovery_target": chosen_slot, "alternative_slot": chosen_slot,
                    "offered_alternatives": offered,
                    "escalation": f"Alternatives for {state['slots'][chosen_slot].label}. Choose a known candidate or search for more. No component has changed yet."}
        if action == REDESIGN:
            nodes._emit(ev.reasoning(None, "Replanning the device using your instruction and the current board."))
            old = current_plan(state)
            finding = state.get("escalation") or "Resolve the current design's missing functions and conflicts."
            proposal = await revision.propose(state.get("prompt", ""), state.get("reconfiguration") or
                                             "Resolve the current conflicts while preserving the requested functions.", old, finding)
            changed = (proposal.replace_slots or proposal.plan.rails != old.rails or proposal.plan.links != old.links
                       or set(proposal.plan.slots) != set(old.slots)
                       or any(proposal.plan.slots[k].constraint != old.slots[k].constraint
                              for k in proposal.plan.slots if k in old.slots))
            if not changed:
                raise ValueError("The redesign proposed no structural change or component replacement. Your board was kept.")
            result = publish(state, proposal.plan, config, replacements=proposal.replace_slots)
            nodes._emit(ev.reasoning(None, proposal.explanation or "Applied the revised plan; compatibility is being rechecked."))
            return result

        if key not in state["slots"]:
            raise ValueError("There is no component slot to replace. Choose Redesign the device.")
        slot = state["slots"][key]
        if action == CONTROLLER:
            board = board_of(state)
            controllers = intended_controllers(board)
            if controllers:
                key = controllers[0]
                slot = state["slots"][key]
                action = SEARCH
            else:
                if state.get("added_slots", 0) >= policy.MAX_ADDED_SLOTS or len(board.slots) >= planner.MAX_SLOTS:
                    raise ValueError("The board has reached its component-addition limit. Choose Redesign the device.")
                rail = board.input_rail(key)
                if rail is None or rail.id == topology.INPUT_RAIL_ID:
                    raise ValueError("The controller's regulated supply is not established. Choose Redesign the device.")
                old = current_plan(state)
                controller_id = nodes._added_slot_id("mcu", old.slots)
                buses = " ".join(slot.part.interfaces[:2]) if slot.part else ""
                added = Slot(controller_id, "Microcontroller", "core", pinned=True, constraint={"category": "mcu"})
                revised = replace(old, slots={**old.slots, controller_id: added},
                                  queries={**old.queries, controller_id: f"microcontroller {buses}".strip()},
                                  rails=[replace(r, members=(*r.members, controller_id)) if r.id == rail.id else r for r in old.rails],
                                  links=(*old.links, *((controller_id, k) for k, s in old.slots.items()
                                                       if s.part and s.part.role == "peripheral")))
                revised = replace(revised, order=planner.placement_order(revised.slots))
                result = publish(state, revised, config)
                return {**result, "added_slots": state.get("added_slots", 0) + 1}

        if action == SEARCH:
            failures = rules.failures(state.get("verdicts", []))
            if needs_controller(state) and failures and key == failures[0].subject:
                raise ValueError("Searching more peripherals will not add a controller. Use Add or replace the controller.")
            nodes._emit(ev.reasoning(key, f"Searching additional candidates for {slot.label}; previously considered parts are excluded."))
            excluded = {c.mpn.casefold() for c in state.get("candidates", {}).get(key, [])}
            excluded.update(str(row.get("mpn") or getattr(row.get("candidate"), "mpn", "")).casefold()
                            for row in state.get("offered_alternatives", []))
            if slot.part:
                excluded.add(slot.part.mpn.casefold())
            found = await sourcing.find(state["plan"].queries.get(key, slot.label),
                                        constraint=slot.constraint, purpose=slot.label, board=state.get("prompt"),
                                        exclude=frozenset(excluded))
            # Also enforce exclusions for alternate sourcing implementations and test providers.
            found = [candidate for candidate in found if candidate.mpn.casefold() not in excluded][:3]
            if not found:
                raise ValueError("No additional catalogue candidates were found. Research alternatives with Exa or redesign the device.")
            offered = [catalogue_offer(candidate, index + 1) for index, candidate in enumerate(found)]
            return {**base, "offered_alternatives": offered, "alternative_slot": key,
                    "recovery_target": key,
                    "candidates": {**state.get("candidates", {}), key: [*state.get("candidates", {}).get(key, []), *found]},
                    "escalation": f"Found {len(offered)} additional candidate(s) for {slot.label}. Choose one to swap in and revalidate."}

        if action == WEB:
            from ..parts.alternatives import investigate
            nodes._emit(ev.reasoning(key, f"Researching {slot.label} beyond JLCPCB with Exa; comparing up to two source documents."))
            excluded = [c.mpn for c in state.get("candidates", {}).get(key, [])]
            previous = state.get("researched_alternatives", {}).get(key, [])
            excluded.extend(row["mpn"] for row in previous)
            if slot.part:
                excluded.append(slot.part.mpn)
            offered, message = await investigate(slot, state.get("prompt", ""), state.get("escalation", ""), exclude=excluded)
            return {**base, "offered_alternatives": offered, "alternative_slot": key, "escalation": message,
                    "researched_alternatives": {**state.get("researched_alternatives", {}), key: [*previous, *offered][-6:]}}

        selected = next((row for row in state.get("offered_alternatives", []) if row["action"] == action), None)
        if selected is None:
            raise ValueError("That alternative is no longer offered. Search again before choosing a component.")
        nodes._emit(ev.reasoning(key, f"Applying your choice for {slot.label}; rechecking the whole board."))
        if selected.get("part"):
            part = selected["part"]
        elif selected.get("candidate"):
            part = await sourcing.choose(selected["candidate"])
        else:
            from ..parts.alternatives import materialize
            part = await materialize(selected)
        slots = {**state["slots"], key: replace(slot, part=part, status="pending", repair_count=slot.repair_count + 1)}
        history = dict(state.get("replacement_history", {}))
        previous = history.get(key, [])
        if slot.part:
            history[key] = [p for p in previous if _part_key(p) != _part_key(slot.part)][-5:] + [slot.part]
        nodes._emit(ev.plan(slots.values(), topology.power_edges(state["rails"])
                           + topology.planned_data_edges(getattr(state["plan"], "links", ())),
                           topology.supply_node(topology.power_source(state["requirements"])), replace_board=True))
        return {**base, "slots": slots, "current": key, "offered_alternatives": [], "alternative_slot": None,
                "replacement_history": history, "pending": [k for k in state.get("pending", []) if k != key],
                "accepted": [], "escalation": None, "revalidate_all": True, "manual_choice": True,
                "unfilled": [k for k in state.get("unfilled", []) if k != key]}
    except ToolError:
        # Upstream tool messages may include raw transport details. Keep the board
        # and choices intact, without exposing or treating those details as evidence.
        message = "The component catalogue could not complete this request. Your board was kept; retry or choose another recovery option."
        nodes._emit(ev.reasoning(None, message, level="warning"))
        return {**base, "escalation": message}
    except (llm.LLMUnavailable, ValueError) as error:
        message = str(error)
        nodes._emit(ev.reasoning(None, message, level="warning"))
        return {**base, "escalation": message}


def after_recovery(state):
    if state.get("escalation"):
        return "escalate"
    return "select" if state.get("pending") and state.get("current") is None else "validate"
