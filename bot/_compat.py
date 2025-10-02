"""Compatibility helpers for Python version differences."""

from __future__ import annotations

import sys
from dataclasses import dataclass as _dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def slotted_dataclass(_cls: T | None = None, **kwargs: Any) -> Callable[[T], T] | T:
    """Use dataclass with slots on Python 3.10+, plain on older versions."""

    if sys.version_info >= (3, 10):
        kwargs.setdefault("slots", True)

    def wrap(cls: T) -> T:
        return _dataclass(**kwargs)(cls)

    if _cls is None:
        return wrap
    return wrap(_cls)

