"""Protocol roles and GPIO demand on explicitly declared signal nets."""
from __future__ import annotations

from collections import Counter

from . import buses
from .models import Board, Verdict

PIN_COSTS = {"I2C": (2, 0), "SMBUS": (2, 0), "SPI": (3, 1), "UART": (0, 2),
             "CAN": (2, 0), "1-WIRE": (1, 0)}


def interfaces(board: Board) -> list[Verdict]:
    findings = []
    connected = set()
    for net in board.signal_nets:
        protocol = buses.canonical_bus(net.protocol)
        keys = tuple(dict.fromkeys(port.component for port in net.ports))
        connected.update(keys)
        controllers = {p.component for p in net.ports if p.role == "controller"}
        duplicate = len(net.ports) != len({(p.component, p.name) for p in net.ports})
        topology_issue = None
        uncertain_topology = False
        if len(keys) < 2:
            topology_issue = "fewer than two distinct components are connected"
        elif not controllers:
            topology_issue = "no controller is connected"
        elif len(controllers) > 1:
            topology_issue = "multiple controllers require an explicit arbitration or bus topology model"
            uncertain_topology = True
        elif protocol == "UART" and len(keys) > 2:
            topology_issue = "a point-to-point UART net has more than two components"
        if duplicate:
            topology_issue = "a port is listed more than once"
            uncertain_topology = True
        for key in keys:
            part = board.part(key)
            ports = [p for p in net.ports if p.component == key]
            issue = topology_issue
            unknown = (net.basis != "confirmed" or not part or not part.interfaces or uncertain_topology
                       or any(board.slots[k].quantity != 1 for k in keys if k in board.slots))
            if part and part.interfaces:
                controller = key in controllers
                supported = any(
                    buses.master_satisfies_bus(protocol, bus) if controller
                    else buses.master_satisfies_bus(bus, protocol)
                    for bus in part.interfaces)
                if not supported:
                    issue = f"{part.mpn} does not support the declared {protocol} connection"
                if controller and part.role == "peripheral":
                    issue = f"{part.mpn} is a peripheral, but is assigned to drive this net"
                if part.role not in {"master", "peripheral"} or any(p.role not in {"controller", "peripheral"} for p in ports):
                    unknown = True
            status = "warn" if unknown else "fail" if issue else "pass"
            detail = issue or ("connection or component capabilities are unverified" if unknown
                              else "declared protocol and roles are compatible; physical pins and timing still require review")
            findings.append(Verdict("interface_role_match", status, f"{net.id}: {detail}.", key,
                involved=keys, scope=net.id, unchecked=bool(unknown),
                evidence=part.cite(key, "interfaces", "role") if part else ()))
    for key, slot in board.slots.items():
        if slot.part and slot.part.interfaces and key not in connected:
            findings.append(Verdict("interface_role_match", "warn",
                "No signal connection has been declared for this component.", key, unchecked=True))
    return findings


def pin_budgets(board: Board) -> list[Verdict]:
    """Each independent bus consumes pins on its actual controller, once per net."""
    controllers = {p.component for net in board.signal_nets for p in net.ports if p.role == "controller"}
    controllers.update(key for key, slot in board.slots.items() if slot.part and slot.part.role == "master")
    counts, uncertain = Counter(), set()
    involved = {key: {key} for key in controllers}
    connected = {p.component for net in board.signal_nets for p in net.ports}
    unconnected = any(slot.part and slot.part.role == "peripheral" and key not in connected
                      for key, slot in board.slots.items())
    for net in board.signal_nets:
        owners = {p.component for p in net.ports if p.role == "controller"}
        members = {p.component for p in net.ports}
        cost = PIN_COSTS.get(buses.canonical_bus(net.protocol))
        verified = net.basis == "confirmed" and len(owners) == 1 and len(members) >= 2
        verified &= len(net.ports) == len({(p.component, p.name) for p in net.ports})
        for owner in owners:
            involved[owner].update(members)
            if cost and verified:
                counts[owner] += cost[0] + cost[1] * len(members - {owner})
            else:
                uncertain.add(owner)
    findings = []
    for key in sorted(controllers):
        part = board.part(key)
        capacity = part.pins_available if part else None
        required = counts[key]
        unknown = (capacity is None or key in uncertain or unconnected or not any(
            p.component == key and p.role == "controller" for net in board.signal_nets for p in net.ports)
            or any(board.slots[k].quantity != 1 for k in involved[key] if k in board.slots))
        failed = capacity is not None and required > capacity
        status = "fail" if failed else "warn" if unknown else "pass"
        detail = (f"At least {required} GPIO required by declared nets; "
                  + ("controller GPIO capacity is unknown." if capacity is None else f"{capacity} available."))
        if unknown and not failed:
            detail += " Unassigned or unverified connections prevent a complete budget."
        findings.append(Verdict("pin_budget", status, detail, key, involved=tuple(sorted(involved[key])),
            unchecked=unknown and not failed, evidence=part.cite(key, "pins_available") if part else ()))
    return findings
