"""Read compatibility for saved runs created before the FUZE package rename.

Checkpoints store fully qualified class names. Resolve those names to the same
class objects used by FUZE; loading a second copy would break isinstance checks.
New code and new checkpoints always use the fuze namespace.
"""

from importlib import import_module
import sys

for _module in ("engine", "engine.models", "planner", "planner.plan", "parts", "parts.search"):
    _canonical = import_module(f"fuze.{_module}")
    sys.modules[f"{__name__}.{_module}"] = _canonical
    if "." not in _module:
        globals()[_module] = _canonical

del _module, _canonical
