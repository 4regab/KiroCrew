"""Per-test reset of the process-global ``kiro_crew.memory_index`` backend registry.

Part of ``test/conftest.py``, which imports and re-binds this name so pytest registers
the fixture as the suite conftest's own; see the import block there. Plain module,
never collected.
"""

from __future__ import annotations

import sys

from conftest_guards import per_test_guard


@per_test_guard
def _reset_memory_index_registry():
    """Start each test with no memory index backend registered beyond the built-ins.

    ``register_index_backend`` writes a module-scope table, so a backend one test
    registered would otherwise be listed and created by name in a later test on
    the same worker.
    """

    def clear():
        module = sys.modules.get("kiro_crew.memory_index.registry")
        if module is not None:
            module._registered.clear()

    clear()
    try:
        yield
    finally:
        clear()
