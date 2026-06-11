"""Make the pure modules importable WITHOUT executing AutoSQUID/__init__.py.

__init__ imports the hardware modules (serial_io -> pyserial, daq -> nidaqmx), which are not
installed off the bench. Registering a stub package whose __path__ points at src/AutoSQUID lets
`from AutoSQUID.scc import ...` resolve the submodule files directly, so the pure modules
(util, scc, analysis, config) are tested against the real source with no hardware deps.
"""
import sys
import types
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parents[1] / "src" / "AutoSQUID"

_pkg = types.ModuleType("AutoSQUID")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["AutoSQUID"] = _pkg
