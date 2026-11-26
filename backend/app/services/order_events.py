"""Tiny in-process event bus. order_service emits; other modules (notifications, rider earnings,
popularity counters) subscribe in event_handlers.py. This keeps order_service free of knowledge
about who cares that an order changed."""
from collections.abc import Callable

_handlers: list[Callable] = []


def subscribe(fn: Callable) -> Callable:
    _handlers.append(fn)
    return fn


def emit(db, order, old_status, new_status, actor_role: str, background=None) -> None:
    for fn in list(_handlers):
        fn(db, order, old_status, new_status, actor_role, background)
