"""Distinguish missing controller structure from missing component evidence."""
from __future__ import annotations

from .models import Board, Verdict


def intended_controllers(board: Board) -> tuple[str, ...]:
    return tuple(key for key, slot in board.slots.items()
                 if (slot.constraint or {}).get("category") == "mcu"
                 or slot.part is not None and slot.part.role == "master")


def uncertain_controllers(board: Board) -> tuple[str, ...]:
    return tuple(key for key in intended_controllers(board)
                 if board.slots[key].part is not None and board.slots[key].part.role is None)


def missing_controller(conflict: Verdict, board: Board) -> bool:
    part = board.part(conflict.subject)
    return (conflict.rule == "interface_role_match" and part is not None
            and part.role == "peripheral" and bool(part.interfaces)
            and not any(slot.part and slot.part.role == "master" for slot in board.slots.values())
            and not uncertain_controllers(board))
