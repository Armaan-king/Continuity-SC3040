"""Regulator-topology facts owned by the constraint engine.

Distributor categories are subcategory labels, not a reliable shared taxonomy. The
engine keeps the observed labels it can interpret here so regulation checks remain
offline and deterministic, independent of the catalogue and planning layers.

An absent or unfamiliar label has no implied electrical behaviour. Callers receive
``None`` in that case and must report an unchecked verdict rather than assuming a
linear regulator.
"""

from __future__ import annotations

from typing import Literal


Regulation = Literal["switching", "linear"]

SWITCHING_TOPOLOGIES = frozenset({"buck", "boost", "buck-boost", "sepic"})

LINEAR_TOPOLOGIES = frozenset({"ldo", "linear"})
"""Topologies that dissipate the difference rather than converting it.

A linear part cannot output more than its input, and R1 fails a part for that. Asserted
only where the part says it is linear, or where its category says so — an *unstated*
topology is not evidence of one, which is the whole reason `regulation()` has a third
answer. Moved here from `rules.py` on 10 Aug when the category became a second source.
"""


def _key(value: str) -> str:
    """Fold category payload whitespace and case without changing its meaning."""
    return " ".join(value.casefold().split())


# Distributor subcategory → electrical regulation type. These labels are observed
# payload values; do not replace this table with a parts-layer taxonomy.
_CATEGORY_REGULATION: dict[str, Regulation] = {
    _key("DC-DC Converters"): "switching",
    _key("Power Over Ethernet (PoE) Controllers"): "switching",
    _key("Voltage Regulators - Linear, Low Drop Out (LDO) Regulators"): "linear",
}


def regulation_from_topology(topology: str | None) -> Regulation | None:
    """Return the stated regulation type, or None when the topology is unknown.

    A part may state more than one topology in the field — JLCPCB publishes
    `Topology: "Boost、Buck"` on six of the regulator rows in `fixtures/`, separated by an
    ideographic comma. Every stated value must be recognised *and* they must agree: a
    part that is a boost or a buck is a switcher either way, while a hypothetical
    "buck、ldo" is two different thermal behaviours and cannot be answered.

    An unrecognised value anywhere makes the whole field unknown, rather than being
    quietly ignored. Reading a stated fact partially is how a wrong verdict starts.
    """
    if topology is None:
        return None
    names = [name.strip().casefold() for name in topology.replace("、", ",").split(",")]
    names = [name for name in names if name]
    if not names or any(
        name not in SWITCHING_TOPOLOGIES and name not in LINEAR_TOPOLOGIES for name in names
    ):
        return None
    facts = {"switching" if name in SWITCHING_TOPOLOGIES else "linear" for name in names}
    return facts.pop() if len(facts) == 1 else None


def regulation_from_category(category: str | None) -> Regulation | None:
    """Return the regulation type implied by an observed distributor category."""
    if category is None:
        return None
    return _CATEGORY_REGULATION.get(_key(category))
