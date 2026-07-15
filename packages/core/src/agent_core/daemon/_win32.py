"""Single import boundary for pywin32 (``win32service`` / ``win32serviceutil``).

pywin32 ships no type stubs, so importing it makes ``mypy --strict`` emit
``import-untyped`` at every call site. Centralising the imports here keeps the
one ignore in a single place; the ``pywin32-isolated`` import-linter contract
forbids importing ``win32*`` anywhere else in agent_core.

Import the modules from here::

    from agent_core.daemon import _win32

    _win32.win32service.OpenSCManager(...)

    class Service(_win32.ServiceFramework):
        ...

The names are typed ``Any`` (pywin32 is untyped regardless), so call sites need
no per-line ignores. On non-Windows pywin32 is absent: the module names are
``None`` and ``ServiceFramework`` falls back to ``object`` so this module
imports on any OS. Every real use site is already guarded by
``sys.platform == "win32"``.
"""
from __future__ import annotations

import sys
from typing import Any

win32service: Any
win32serviceutil: Any
ServiceFramework: Any

if sys.platform == "win32":
    import win32service as _win32service  # type: ignore[import-untyped]
    import win32serviceutil as _win32serviceutil  # type: ignore[import-untyped]

    win32service = _win32service
    win32serviceutil = _win32serviceutil
    ServiceFramework = _win32serviceutil.ServiceFramework
else:  # pragma: no cover - non-Windows: pywin32 is not installed
    win32service = None
    win32serviceutil = None
    ServiceFramework = object
