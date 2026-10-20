"""Compatibility facade for the original collector.db module.

All callers may continue to use from collector import db. DB_PATH remains a mutable
module attribute for tests and tooling; assignment is forwarded to the shared runtime.
"""
# ruff: noqa: F403

from __future__ import annotations

import sys
from types import ModuleType

from . import _core
from ._core import *
from .assets import *
from .changes import *
from .collection import *
from .diagnosis import *
from .knowledge import *
from .operations import *
from .schema import *
from .terminal import *


class _PersistenceModule(ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if name in {"DB_PATH", "SCHEMA_VERSION"}:
            setattr(_core, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _PersistenceModule
