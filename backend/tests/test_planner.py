"""What the planner may decide, and what it may not.

The planner is the second place a model's output reaches the engine. The fence is the
same one the normaliser has: classify into vocabularies the engine owns, never supply
an operand, and anything malformed is dropped rather than repaired.
"""

from __future__ import annotations

import pytest

from continuity.engine.models import Requirements
from continuity.planner import plan
from continuity.planner import plan as planner

GOOD = {
    "input_source": "usb-5v",
    "temp_range": [0, 70],
    "current_margin": 0.15,
    "priority": "cost",
    "min_stock": 500,
    "slots": [
        {"id": "regulator", "label": "Regulator", "tier": "power", "pinned": False, "query": "3.3V LDO regulator"},
        {"id": "mcu", "label": "Microcontroller", "tier": "core", "pinned": True, "query": "ESP32 module"},
        {"id": "imu", "label": "IMU", "tier": "peripherals", "pinned": True, "query": "IMU accelerometer"},
    ],
    "rails": [{"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["mcu", "imu"]}],
    "links": [["mcu", "imu"]],
}


def test_a_well_formed_plan_survives_intact():
    plan = planner.build_plan(GOOD)

    assert set(plan.slots) == {"regulator", "mcu", "imu"}
    assert plan.rails[0].members == ("mcu", "imu")
    assert plan.queries["mcu"] == "ESP32 module"
    assert plan.requirements.min_stock == 500
    assert plan.requirements.priority == "cost"


def test_power_is_placed_before_what_draws_from_it():
    """A rail's regulator must exist before the first current check runs on it."""
    assert planner.build_plan(GOOD).order[0] == "regulator"


def test_declared_order_is_kept_within_a_tier():
    """Sorting alphabetically would make placement depend on what slots are named."""
    payload = {**GOOD, "slots": [
        GOOD["slots"][0], GOOD["slots"][1],
        {"id": "zzz", "label": "Z", "tier": "peripherals", "pinned": True, "query": "z"},
        {"id": "aaa", "label": "A", "tier": "peripherals", "pinned": True, "query": "a"},
    ], "rails": [{"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["mcu", "zzz", "aaa"]}]}

    assert planner.build_plan(payload).order[-2:] == ("zzz", "aaa")


# ── the fence ─────────────────────────────────────────────────────────────────


def test_a_rail_naming_a_slot_that_does_not_exist_is_dropped():
    """A phantom slot would have every rule on that rail reasoning about a part that
    is never placed."""
    payload = {**GOOD, "rails": [
        {"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["mcu", "ghost"]},
        {"id": "1V8", "voltage": 1.8, "source": "nonexistent", "members": ["mcu"]},
    ]}

    plan = planner.build_plan(payload)

    assert [r.id for r in plan.rails] == ["3V3"]
    assert plan.rails[0].members == ("mcu",)


def test_a_slot_with_an_invented_tier_is_dropped():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "mystery", "label": "?", "tier": "quantum", "pinned": True, "query": "x"}]}

    assert "mystery" not in planner.build_plan(payload).slots


def test_a_power_slot_cannot_be_pinned_by_the_planner_reply():
    payload = {
        **GOOD,
        "slots": [{**GOOD["slots"][0], "pinned": True}, *GOOD["slots"][1:]],
    }

    assert not planner.build_plan(payload).slots["regulator"].pinned


def test_a_user_named_core_slot_stays_pinned():
    assert planner.build_plan(GOOD).slots["mcu"].pinned


def test_an_unsafe_slot_id_is_dropped():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "../etc/passwd", "label": "x", "tier": "core", "pinned": False, "query": "x"}]}

    assert len(planner.build_plan(payload).slots) == 3


def test_a_slot_id_starting_with_a_digit_keeps_its_rail():
    """On 10 Aug, ``3v3_rail`` was dropped, then its rail was dropped with it.

    The identifier is renamed for DOM safety; no electrical fact is repaired.
    """
    payload = {
        **GOOD,
        "slots": [
            {**GOOD["slots"][0], "id": "3v3_rail", "label": "3.3V Regulator"},
            {**GOOD["slots"][1], "id": "3v3_mcu"},
            GOOD["slots"][2],
        ],
        "rails": [
            {"id": "3V3", "voltage": 3.3, "source": "3v3_rail", "members": ["3v3_mcu", "imu"]}
        ],
        "links": [["3v3_rail", "3v3_mcu"], ["3v3_mcu", "3v3_rail"]],
    }

    built = planner.build_plan(payload)

    assert built is not None
    assert "s_3v3_rail" in built.slots
    assert built.queries["s_3v3_rail"] == "3.3V LDO regulator"
    assert built.rails[0].source == "s_3v3_rail"
    assert built.rails[0].members == ("s_3v3_mcu", "imu")
    assert built.links == (
        ("s_3v3_rail", "s_3v3_mcu"),
        ("s_3v3_mcu", "s_3v3_rail"),
    )


def test_an_unsalvageable_slot_id_is_still_dropped():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "3v3-rail", "label": "x", "tier": "core", "pinned": False, "query": "x"}]}

    assert "s_3v3-rail" not in planner.build_plan(payload).slots


def test_an_empty_slot_id_is_still_dropped():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "", "label": "x", "tier": "core", "pinned": False, "query": "x"}]}

    assert "s_" not in planner.build_plan(payload).slots


def test_a_normalised_slot_id_collision_keeps_the_first_slot():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "s_3v3_rail", "label": "Existing", "tier": "core", "pinned": False, "query": "x"},
        {"id": "3v3_rail", "label": "Replacement", "tier": "power", "pinned": False, "query": "y"},
    ]}

    assert planner.build_plan(payload).slots["s_3v3_rail"].label == "Existing"


def test_an_input_source_outside_the_vocabulary_becomes_unresolved():
    """Never a guessed voltage — an unknown supply is a question for the user."""
    plan = planner.build_plan({**GOOD, "input_source": "9V solar panel maybe"})

    assert plan.requirements.input_source == planner.UNRESOLVED


def test_a_nonsense_margin_falls_back_to_the_default():
    assert planner.build_plan({**GOOD, "current_margin": 15}).requirements.current_margin == 0.15
    assert planner.build_plan({**GOOD, "current_margin": -1}).requirements.current_margin == 0.15


def test_an_inverted_temp_range_is_refused():
    assert planner.build_plan({**GOOD, "temp_range": [85, -40]}).requirements.temp_range == (0, 70)


def test_a_plan_with_no_rails_is_a_direct_drive_board():
    """A no-rail reply used to mean no plan; coin-cell BLE boards changed that.

    With four real slots and no regulator, the board input is the honest supply rail.
    Almost no slots is still no plan, as the test below keeps asserting.
    """
    payload = {
        **GOOD,
        "slots": [
            *GOOD["slots"],
            {"id": "crystal", "label": "Crystal", "tier": "passives", "pinned": False,
             "query": "crystal resonator"},
        ],
        "rails": [],
    }

    built = planner.build_plan(payload)

    assert built is not None
    assert built.rails == []


def test_graph_planning_keeps_direct_drive_slots_on_the_input_rail(monkeypatch):
    """The graph node rebuilds rails, so it must retain direct-drive VIN membership."""
    from continuity.graph import nodes

    class Events:
        def plan(self, *args):
            return None

        def reasoning(self, *args):
            return None

    board_plan = planner.build_plan(
        {
            **GOOD,
            "slots": [
                *GOOD["slots"],
                {"id": "crystal", "label": "Crystal", "tier": "passives", "pinned": False,
                 "query": "crystal resonator"},
            ],
            "rails": [],
        }
    )
    monkeypatch.setattr(nodes, "_emit", lambda payload: None)

    state = nodes.plan(
        {"plan": board_plan, "requirements": board_plan.requirements},
        {"configurable": {"events": Events()}},
    )

    assert state["rails"]["VIN"].members == tuple(board_plan.slots)


def test_a_plan_with_almost_no_slots_is_no_plan():
    """Direct-drive is a topology, but one surviving slot remains too little to plan."""
    assert planner.build_plan({**GOOD, "slots": GOOD["slots"][:1]}) is None


def test_slot_count_is_bounded():
    many = [{"id": f"s{i}", "label": f"S{i}", "tier": "peripherals", "pinned": False, "query": "x"}
            for i in range(40)]
    payload = {**GOOD, "slots": GOOD["slots"] + many}

    assert len(planner.build_plan(payload).slots) <= planner.MAX_SLOTS


def test_duplicate_slot_ids_keep_the_first():
    payload = {**GOOD, "slots": GOOD["slots"] + [
        {"id": "mcu", "label": "Impostor", "tier": "core", "pinned": False, "query": "x"}]}

    assert planner.build_plan(payload).slots["mcu"].label == "Microcontroller"


def test_a_link_to_a_missing_slot_is_dropped():
    assert planner.build_plan({**GOOD, "links": [["mcu", "ghost"], ["mcu", "imu"]]}).links == (("mcu", "imu"),)


def test_a_peripheral_without_a_controller_still_builds_and_keeps_the_brief():
    """Rejecting an incomplete plan is worse than accepting one, and this pins why.

    Completeness is enforced in the prompt, not here. A structural rejection was tried
    on 11 Aug and reverted: `build_plan` returning `None` sends the caller to
    `fallback_plan`, which is a keyword-matched board carrying only `input_source`. So
    rejecting this payload silently downgraded an **industrial** brief to commercial
    `(0, 70)` and a stated `min_stock` of 5000 to the default 100 — the exact false pass
    this engine exists to prevent, introduced while fixing something else.

    The board that reaches the engine here is genuinely incomplete, and R2 says so:
    *"needs I2C but the board has no controller to drive it"*. A reported incompleteness
    beats a silently rewritten requirement.
    """
    payload = {
        **GOOD,
        "slots": [
            GOOD["slots"][0],
            {"id": "display", "label": "OLED Display", "tier": "peripherals",
             "pinned": True, "query": "OLED display", "category": "display"},
        ],
        "rails": [
            {"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["display"]}
        ],
        "links": [["mcu", "display"]],
        "temp_range": [-40, 85],
        "min_stock": 5000,
    }

    plan = planner.build_plan(payload)

    assert plan is not None
    assert plan.requirements.temp_range == (-40, 85), "the brief’s grade must survive"
    assert plan.requirements.min_stock == 5000, "the brief's volume must survive"


def test_a_controller_and_its_linked_peripheral_still_build():
    plan = planner.build_plan(GOOD)

    assert plan is not None
    assert plan.links == (("mcu", "imu"),)


def test_a_small_power_only_board_still_builds():
    payload = {
        "slots": [
            {"id": "regulator", "label": "Regulator", "tier": "power",
             "pinned": False, "query": "3.3V LDO regulator", "category": "regulator"},
            {"id": "load", "label": "Status LED", "tier": "passives",
             "pinned": True, "query": "indicator LED", "category": "led"},
        ],
        "rails": [
            {"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["load"]}
        ],
        "links": [],
    }

    plan = planner.build_plan(payload)

    assert plan is not None
    assert plan.links == ()


def test_garbage_is_not_a_plan():
    assert planner.build_plan({}) is None
    assert planner.build_plan({"slots": "not a list", "rails": 5}) is None


# ── the fallback ──────────────────────────────────────────────────────────────


def test_the_fallback_reads_what_it_can_and_admits_the_rest():
    """No key is a degraded mode. It classifies the supply or says unresolved."""
    plan = planner.fallback_plan("temp and humidity logger with an OLED, usb powered")

    assert plan.requirements.input_source == "usb-5v"
    assert {"regulator", "mcu", "sensor", "display"} == set(plan.slots)
    assert plan.order[0] == "regulator"


def test_the_fallback_never_invents_a_supply():
    plan = planner.fallback_plan("a board powered by a hand-cranked dynamo")

    assert plan.requirements.input_source == planner.UNRESOLVED


def test_the_fallback_returns_unresolved_without_a_recognisable_supply():
    assert planner._guess_source("a battery-operated weather station") == planner.UNRESOLVED


def test_the_fallback_does_not_match_supply_keywords_inside_other_words():
    assert planner._guess_source("a poetry display controller") == planner.UNRESOLVED


def test_the_fallback_does_not_choose_between_multiple_named_supplies():
    assert planner._guess_source("a board with usb and 12v barrel power") == planner.UNRESOLVED


# ── named replacements are fenced against what search returned ────────────────


def test_a_named_replacement_must_be_one_the_reviewer_was_shown():
    """A fence, not a lookup: the model may only point at parts it actually saw."""
    from continuity.graph.nodes import _named_candidate
    from continuity.parts.search import Candidate

    def candidate(mpn):
        return Candidate(lcsc="C1", mpn=mpn, manufacturer="x", description="", package=None,
                         category="", subcategory="", stock=100, unit_price=1.0,
                         library_type="basic")

    shown = [candidate("STM32F103C8T6"), candidate("STM8S003F3P6TR")]

    assert _named_candidate({"mpn": "stm8s003f3p6tr"}, shown).mpn == "STM8S003F3P6TR"
    assert _named_candidate({"mpn": "HALLUCINATED-PART"}, shown) is None
    assert _named_candidate({}, shown) is None


# ── a stated voltage short-circuits the question ──────────────────────────────


def test_a_stated_voltage_needs_no_clarification():
    """"48V industrial bus" is unambiguous, and no entry in the vocabulary matches it.

    Asking would send the user to a list that cannot express what they just said.
    """
    from continuity.graph import nodes

    state = {"requirements": Requirements(input_source="unresolved", input_voltage=48.0)}

    assert nodes.needs_clarification(state) == "plan"


def test_an_unnamed_supply_with_no_voltage_still_asks():
    from continuity.graph import nodes

    state = {"requirements": Requirements(input_source="unresolved")}

    assert nodes.needs_clarification(state) == "clarify"


def test_an_absurd_voltage_is_dropped_rather_than_used():
    assert plan._clean_requirements({"input_voltage": 100000}).input_voltage is None
    assert plan._clean_requirements({"input_voltage": -5}).input_voltage is None
    assert plan._clean_requirements({"input_voltage": "12V"}).input_voltage is None


def test_a_plausible_voltage_survives():
    assert plan._clean_requirements({"input_voltage": 7.4}).input_voltage == 7.4


# ── a planned topology must filter, not just phrase ───────────────────────────
#
# Soil-logger run, 9 Aug: the planner asked for a "Boost Converter" and the first search
# returned XL1509-5.0E1, a buck. JLCPCB's text search ignores the word (measured), so
# every board needing a boost burned a full repair cycle before it could start. The
# repair path already pushes topology down as a spec filter; placement did not.


def test_a_planned_topology_becomes_a_slot_constraint():
    plan_obj = plan.build_plan(
        {
            "slots": [
                {"id": "reg", "tier": "power", "label": "Boost Converter",
                 "query": "boost converter", "topology": "boost"},
                {"id": "mcu", "tier": "core", "label": "MCU", "query": "mcu"},
            ],
            "rails": [{"id": "3V3", "voltage": 3.3, "source": "reg", "members": ["mcu"]}],
        }
    )

    assert plan_obj is not None
    # The constraint also carries what the rail requires — see `_constrain_rail_sources`.
    # This test is about the topology travelling out of the label and into the search.
    assert plan_obj.slots["reg"].constraint["topology"] == "boost"
    assert plan_obj.slots["mcu"].constraint is None


def test_a_topology_outside_the_vocabulary_is_not_carried():
    plan_obj = plan.build_plan(
        {
            "slots": [
                {"id": "reg", "tier": "power", "query": "regulator", "topology": "magic"},
                {"id": "mcu", "tier": "core", "query": "mcu"},
            ],
            "rails": [{"id": "3V3", "voltage": 3.3, "source": "reg", "members": ["mcu"]}],
        }
    )

    assert "topology" not in (plan_obj.slots["reg"].constraint or {})


# ── battery margin, enforced rather than requested ────────────────────────────
#
# The prompt has always said "0.30 for battery or low-power designs". The BLE beacon run
# on 8 Aug came back with 0.15 anyway. It reads correctly now, but that is the model
# choosing to comply — nothing stopped it doing otherwise, and a headroom figure that
# depends on a model's mood is not a design rule.
#
# The input source already says whether this is a battery. Derive the floor from it.


def test_a_battery_supply_forces_the_wider_margin():
    for source in ("battery-3v0", "battery-aa", "battery-3v7", "9v-battery"):
        reqs = plan._clean_requirements({"input_source": source, "current_margin": 0.15})

        assert reqs.current_margin == 0.30, f"{source} must not run on 15% headroom"


def test_a_mains_supply_keeps_the_planner_figure():
    reqs = plan._clean_requirements({"input_source": "usb-5v", "current_margin": 0.15})

    assert reqs.current_margin == 0.15


def test_a_wider_margin_than_the_battery_floor_survives():
    """The floor raises, it never lowers — a deliberate 40% stays 40%."""
    reqs = plan._clean_requirements({"input_source": "battery-3v0", "current_margin": 0.40})

    assert reqs.current_margin == 0.40


# ── the planner already knows what each regulator must do ─────────────────────
#
# 48 V board: the planner printed "3V3 at 3.3 V, fed from 48.0 V" and then searched for a
# regulator with no rating attached, got a 40 V part, and spent two repairs rediscovering
# its own arithmetic. 18650 board: same, one repair, for a fixed 5 V part on a 3.3 V rail.
#
# Derived rather than prompted — the rails and the supply are already declared, so this
# needs no model compliance.


def rail_plan(**over):
    raw = {
        "input_source": "12v-barrel",
        "slots": [
            {"id": "reg", "tier": "power", "label": "Buck", "query": "buck regulator",
             "topology": "buck"},
            {"id": "mcu", "tier": "core", "label": "MCU", "query": "mcu"},
        ],
        "rails": [{"id": "3V3", "voltage": 3.3, "source": "reg", "members": ["mcu"]}],
    }
    raw.update(over)
    return plan.build_plan(raw)


def test_the_rail_voltage_becomes_the_sources_output_constraint():
    reg = rail_plan().slots["reg"]

    assert reg.constraint["vout"] == 3.3


def test_the_supply_voltage_becomes_the_input_constraint():
    """The regulator hangs off the board input, so it has to accept it."""
    reg = rail_plan().slots["reg"]

    assert reg.constraint["vin_min"] == 12.0


def test_a_stated_voltage_drives_the_input_constraint():
    reg = rail_plan(input_source="unresolved", input_voltage=48.0).slots["reg"]

    assert reg.constraint["vin_min"] == 48.0


def test_a_slot_that_sources_nothing_gets_no_rail_constraint():
    mcu = rail_plan().slots["mcu"]

    assert mcu.constraint is None


def test_an_unresolved_supply_constrains_the_output_only():
    """Nothing is known about the input yet, so nothing is claimed about it."""
    reg = rail_plan(input_source="unresolved").slots["reg"]

    assert reg.constraint["vout"] == 3.3
    assert "vin_min" not in reg.constraint


def test_a_regulator_fed_by_another_regulator_takes_no_input_constraint():
    """A cascade's second stage is fed by the first, not by the board input."""
    plan_obj = plan.build_plan(
        {
            "input_source": "24v-industrial",
            "slots": [
                {"id": "r1", "tier": "power", "query": "buck regulator", "topology": "buck"},
                {"id": "r2", "tier": "power", "query": "ldo regulator", "topology": "ldo"},
                {"id": "mcu", "tier": "core", "query": "mcu"},
            ],
            "rails": [
                {"id": "5V", "voltage": 5.0, "source": "r1", "members": ["r2"]},
                {"id": "3V3", "voltage": 3.3, "source": "r2", "members": ["mcu"]},
            ],
        }
    )

    assert plan_obj.slots["r1"].constraint["vin_min"] == 24.0
    assert "vin_min" not in plan_obj.slots["r2"].constraint
    assert plan_obj.slots["r2"].constraint["vout"] == 3.3


# ── what kind of part a slot is ───────────────────────────────────────────────
#
# A regulator slot carries {topology, vin_min, vout} and returns six correct parts; the
# same slot as plain text returns one screw terminal. Peripheral slots carried a string
# and nothing else. Measured 10 Aug against the live API:
#
#     'environmental sensor' → 0 results, so `search` fell back to its first word
#     'environmental'        → Circuit Protection / Fuseholders
#                              Connectors / Female Headers
#                              Hardware Fasteners / Metal Products SMT Copper Sheet
#
# "Environmentally" matched RoHS marketing copy. The slot had no way to say *I am a
# sensor, not a fuse clip*. `category` is the peripheral equivalent of `Topology`.


def _with_category(category: object) -> dict:
    return {
        **GOOD,
        "slots": [
            *GOOD["slots"][:2],
            {**GOOD["slots"][2], "category": category},
        ],
    }


def test_a_slot_category_becomes_a_constraint():
    built = planner.build_plan(_with_category("sensor"))

    assert built.slots["imu"].constraint == {"category": "sensor"}


def test_an_invented_category_is_dropped_rather_than_repaired():
    """Same fence as every other vocabulary: classify into ours, or say nothing."""
    built = planner.build_plan(_with_category("vibes"))

    assert built.slots["imu"].constraint is None


def test_a_missing_category_is_not_an_error():
    """The planner may omit it. An unconstrained slot searches exactly as it does today."""
    assert planner.build_plan(GOOD).slots["imu"].constraint is None


def test_a_regulator_keeps_its_category_alongside_the_derived_ratings():
    """`_constrain_rail_sources` merges vout/vin_min in afterwards. It must not drop
    what the planner already said the part *is* — that is the bug `merge_constraints`
    was written for, one layer up."""
    raw = {
        **GOOD,
        "slots": [
            {**GOOD["slots"][0], "topology": "buck", "category": "regulator"},
            *GOOD["slots"][1:],
        ],
    }

    constraint = planner.build_plan(raw).slots["regulator"].constraint

    assert constraint["category"] == "regulator"
    assert constraint["topology"] == "buck"
    assert constraint["vout"] == 3.3


def test_the_prompt_offers_exactly_the_categories_the_validator_accepts():
    """A name in the prompt with no entry in the table is a constraint the system
    accepts and then silently ignores — the failure mode `_push_down` documents for
    spec filters. Generate the list; never type it twice."""
    from continuity.parts import categories

    for name in categories.CATEGORIES:
        assert name in planner.SYSTEM, f"{name} is accepted but never offered"


def test_the_keyless_fallback_still_says_what_each_slot_is():
    """The fallback runs when there is no key *and* when planning failed — the two
    moments a fuse clip in a sensor slot is least likely to be noticed."""
    from continuity.parts import categories

    built = planner.fallback_plan("environmental monitor with an OLED display")

    assert built.slots["sensor"].constraint == {"category": "sensor"}
    assert built.slots["display"].constraint == {"category": "display"}
    assert built.slots["mcu"].constraint == {"category": "mcu"}
    assert all(
        (s.constraint or {}).get("category") in categories.CATEGORIES
        for s in built.slots.values()
    ), "every fallback slot is hardcoded, so every one of them is knowable"
