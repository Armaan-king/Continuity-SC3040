"""The slot-category vocabulary, and what it may reject.

`categories.satisfies` is a filter over the distributor's own category on every candidate.
It rejects only a *provable* mismatch: `None` means the payload did not say, which keeps
the part. Widening an entry is therefore cheap and narrowing one is not — a name missing
here removes real parts from every shortlist that asks for it.
"""

from __future__ import annotations

import pytest

from continuity.parts import categories


# ── a WiFi module is an MCU ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "published",
    ["IoT/Communication Modules", "RF and Wireless", "Embedded Processors & Controllers"],
)
def test_an_mcu_slot_accepts_the_categories_esp32_parts_are_actually_filed_under(published):
    """Measured 12 Aug: query "ESP32 module" returns 20 candidates, 19 stating a
    transmit current, and an `mcu` slot kept **zero** of them.

    Every ESP32 module is filed under `IoT/Communication Modules`; only bare chips sit
    under `Embedded Processors & Controllers`, and bare chips publish no supply current.
    So the filter silently traded the board's whole current budget for a tidier taxonomy,
    and R4 and R5 had nothing left to judge.
    """
    assert categories.satisfies("mcu", published) is True


def test_an_mcu_slot_still_rejects_something_that_is_plainly_not_one():
    assert categories.satisfies("mcu", "Resistors") is False
    assert categories.satisfies("mcu", "Connectors") is False


def test_a_battery_holder_rescue_widens_to_battery_parts_not_pin_headers():
    """The demo board asked for a Li-ion holder and was given a CR1220 coin cell.

    `connector` already accepted `Industrial control electrical`, which is where JLCPCB
    files battery holders, so the *filter* was never the problem. The rescue list had no
    battery entry, so a thin shortlist widened to `Pin Headers` — the first name declared
    — and pulled in the wrong chemistry entirely.
    """
    from continuity.graph.sourcing import _rescue_subcategory

    assert _rescue_subcategory("Li-ion battery holder", {"category": "connector"}) == (
        "Battery connector"
    )
    assert _rescue_subcategory("18650 battery holder", {"category": "connector"}) == (
        "Battery connector"
    )


def test_a_header_query_still_rescues_to_headers():
    from continuity.graph.sourcing import _rescue_subcategory

    assert _rescue_subcategory("2.54mm pin headers", {"category": "connector"}) == "Pin Headers"


# ── the rescue may not answer a question it did not understand ─────────────────


def _candidate(mpn: str, subcategory: str):
    from continuity.parts.search import Candidate

    return Candidate(
        lcsc="C1", mpn=mpn, manufacturer="x", description="", package="SOT-23",
        category="Power Management (PMIC)", subcategory=subcategory,
        stock=1000, unit_price=1.0, library_type="basic", specs={},
    )


def test_a_gps_query_reaches_the_shelf_the_distributor_calls_gnss():
    """The failure this exists for: a GPS slot filled with an ESP32 WiFi module.

    `GPS` appears in no shelf name, because JLCPCB files receivers under **GNSS Modules**.
    Every radio shelf therefore scored zero, `max` returned the first — `WiFi Modules` —
    and the rescue supplied ESP32s to a slot asking for a GPS receiver. Both electrical
    checks and the BOM passed it.
    """
    from continuity.graph.sourcing import _rescue_subcategory

    assert _rescue_subcategory("GPS receiver module", {"category": "radio"}) == "GNSS Modules"
    assert _rescue_subcategory("GPS module", {"category": "radio"}) == "GNSS Modules"


def test_an_unmatched_rescue_is_refused_where_the_shelves_are_different_kinds():
    """WiFi, LoRa and GNSS are not interchangeable, so declaration order is a wrong answer.

    A cellular modem has no shelf in this list at all, so there is nothing to widen to and
    the honest move is to widen to nothing. Refusing costs a thin shortlist and at worst an
    unfilled slot — visibly incomplete, rather than invisibly wrong.
    """
    from continuity.graph.sourcing import _rescue_subcategory

    assert _rescue_subcategory("cellular NB-IoT modem", {"category": "radio"}) is None
    # A query that *does* name one still gets it — the refusal is not a blanket off switch.
    assert _rescue_subcategory("LoRa module", {"category": "radio"}) == "LoRa Modules"
    assert _rescue_subcategory("RF transceiver", {"category": "radio"}) == "RF Transceiver ICs"


def test_an_unmatched_sensor_rescue_still_widens_because_any_sensor_shelf_is_a_sensor():
    """The case the fallback was written for, and the only one that keeps it."""
    from continuity.graph.sourcing import _rescue_subcategory

    assert _rescue_subcategory("environmental sensor", {"category": "sensor"}) is not None


def test_a_charger_slot_is_its_own_kind_of_part():
    """`regulator` used to claim chargers, which made a DC-DC converter a correct answer."""
    assert "charger" in categories.CATEGORIES
    assert "charger" not in categories.CATEGORIES["regulator"].hint.split("—")[0]
    assert categories.CATEGORIES["charger"].defining_shelves


def test_a_converter_is_dropped_from_a_charger_shortlist_while_a_charger_remains():
    """TPS631000DRLR — a buck-boost — reached a solar board's `battery_charger` slot."""
    from continuity.graph.sourcing import viable

    charger = _candidate("TP4057-42-SOT26-R", "Battery Management")
    converter = _candidate("TPS631000DRLR", "DC-DC Converters")

    assert viable([converter, charger], {"category": "charger"}) == [charger]


def test_a_charger_shortlist_with_nothing_on_the_shelf_keeps_what_it_has():
    """Never empty the list: a vocabulary that is wrong about where a part lives should
    cost a worse ordering, not an unfilled slot."""
    from continuity.graph.sourcing import viable

    converter = _candidate("TPS631000DRLR", "DC-DC Converters")

    assert viable([converter], {"category": "charger"}) == [converter]


# ── the last filter: is this the right KIND of part ───────────────────────────
#
# The category vocabulary is one level coarser than the distinction that decides whether
# a part can do the job: `regulator` covers a buck and a PoE controller alike, `clock`
# covers an RTC and a crystal alike. A PoE camera board reached the BOM with an 80 V buck
# as its PoE controller and an 8 MHz crystal as its real-time clock, every electrical
# check passing, because nothing asked what kind of part it was.


def _shortlist():
    return [
        _candidate("XL7005A", "DC-DC Converters"),
        _candidate("TPS23755", "Power Over Ethernet (PoE)"),
    ]


def test_a_candidate_of_the_wrong_kind_goes_to_the_back_and_is_not_removed():
    """A wrong removal costs a part the board needed; a wrong demotion costs a position."""
    from continuity.graph.sourcing import rank_by_fitness

    ordered, demoted = rank_by_fitness(_shortlist(), {"XL7005A"})

    assert [c.mpn for c in ordered] == ["TPS23755", "XL7005A"]
    assert demoted == ("XL7005A",), "and it says so, rather than reordering silently"


def test_every_candidate_being_suspect_still_removes_nothing():
    """Nothing better to put in front of them — but the reader is told, because it means
    the search itself was aimed wrong."""
    from continuity.graph.sourcing import rank_by_fitness

    ordered, demoted = rank_by_fitness(_shortlist(), {"XL7005A", "TPS23755"})

    assert [c.mpn for c in ordered] == ["XL7005A", "TPS23755"]
    assert demoted == ("XL7005A", "TPS23755")


def test_rejecting_nothing_leaves_the_shortlist_and_reports_nothing():
    from continuity.graph.sourcing import rank_by_fitness

    ordered, demoted = rank_by_fitness(_shortlist(), set())

    assert [c.mpn for c in ordered] == ["XL7005A", "TPS23755"]
    assert demoted == ()


def test_a_classifier_that_is_unavailable_rejects_nothing(monkeypatch):
    """Every failure — outage, timeout, malformed JSON — must leave today's behaviour."""
    import asyncio
    from continuity import interpret, llm

    monkeypatch.setattr(llm, "available", lambda: False)

    assert asyncio.run(
        interpret.unfit_candidates(
            purpose="PoE Controller", query="PoE PD controller", candidates=_shortlist()
        )
    ) == set()


def test_an_invented_mpn_cannot_remove_a_real_one(monkeypatch):
    """The classifier may only name candidates it was actually offered."""
    import asyncio
    from continuity import interpret, llm

    async def reply(*_args, **_kwargs):
        return {"rejected": ["NOT-A-CANDIDATE", "XL7005A"]}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(interpret.llm, "complete_json", reply)

    unfit = asyncio.run(
        interpret.unfit_candidates(
            purpose="PoE Controller", query="PoE PD controller", candidates=_shortlist()
        )
    )

    assert unfit == {"XL7005A"}


def test_a_malformed_classifier_reply_rejects_nothing(monkeypatch):
    import asyncio
    from continuity import interpret, llm

    async def reply(*_args, **_kwargs):
        return {"rejected": "XL7005A"}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(interpret.llm, "complete_json", reply)

    assert asyncio.run(
        interpret.unfit_candidates(
            purpose="PoE Controller", query="PoE PD controller", candidates=_shortlist()
        )
    ) == set()


# ── a driver lives on a driver shelf, or it is not a driver ───────────────────


def test_a_logic_gate_on_the_parent_motor_shelf_is_dropped_for_a_real_driver():
    """The stepper board that placed four AND gates and passed every check.

    JLCPCB files `TC7S08FU(TE85L,F)` — a Toshiba 2-input AND gate in a 5-pin SOT-353 —
    under `Motor Driver ICs` with empty `specs` and the description "SOT-353 Motor Driver
    ICs ROHS". Accepted category, plausible shelf, nothing in the payload to argue with.
    The four real shelves are the category's definition; the parent is where the
    mislabelled collect.
    """
    from continuity.graph.sourcing import _on_defining_shelf

    gate = _candidate("TC7S08FU(TE85L,F)", "Motor Driver ICs")
    driver = _candidate("TMC2209", "Stepper Motor Driver")

    kept = _on_defining_shelf([gate, driver], {"category": "motor_driver"})

    assert [c.mpn for c in kept] == ["TMC2209"]


def test_a_thin_driver_shortlist_is_still_kept_rather_than_emptied():
    """A vocabulary wrong about where a part lives must cost ordering, not the slot."""
    from continuity.graph.sourcing import _on_defining_shelf

    gate = _candidate("TC7S08FU(TE85L,F)", "Motor Driver ICs")

    assert _on_defining_shelf([gate], {"category": "motor_driver"}) == [gate]


def test_nothing_on_a_driver_shelf_triggers_the_rescue():
    from continuity.graph.sourcing import _misses_defining_shelf

    gate = _candidate("TC7S08FU(TE85L,F)", "Motor Driver ICs")
    driver = _candidate("TMC2209", "Stepper Motor Driver")

    assert _misses_defining_shelf([gate], {"category": "motor_driver"}) is True
    assert _misses_defining_shelf([gate, driver], {"category": "motor_driver"}) is False


def test_the_shelf_filter_decides_on_the_merged_list_not_each_half():
    """Why declaring the shelves defining was not on its own enough.

    `_on_defining_shelf` runs inside `viable()`, which is called twice over two lists that
    never meet: the hits that triggered the rescue, and the rescue itself. Each was
    compared only against its own members, so a rescue that found real drivers appended
    them *behind* the parent-shelf parts and the shortlist slice kept the wrong ones. A
    motor brief still placed a 2-input AND gate with the shelves already declared.
    """
    from continuity.graph.sourcing import _on_defining_shelf

    hits = [_candidate("TC7S08FU(TE85L,F)", "Motor Driver ICs")]
    rescued = [_candidate("TMC2209", "Stepper Motor Driver")]

    # Each half on its own keeps everything — neither can see the other.
    assert _on_defining_shelf(list(hits), {"category": "motor_driver"}) == hits
    assert _on_defining_shelf(list(rescued), {"category": "motor_driver"}) == rescued

    # Merged, the real driver wins and the mislabelled part goes.
    merged = _on_defining_shelf(hits + rescued, {"category": "motor_driver"})
    assert [c.mpn for c in merged] == ["TMC2209"]
