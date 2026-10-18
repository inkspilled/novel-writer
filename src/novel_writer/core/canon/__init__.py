"""正典层：append-only 事件日志。"""
from .events import CanonEvent, CanonLog, seed_event

__all__ = ["CanonEvent", "CanonLog", "seed_event"]
