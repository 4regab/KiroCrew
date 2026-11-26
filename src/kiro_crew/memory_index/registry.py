"""Which memory index backends exist, by name.

Built-in backends ship here. Anything else (an app, a deployment's own hosted
index) registers a factory under its own name, the same shape as
``embeddings_shared.register_embedding_backend``. A name that shadows a
built-in is refused, so ``exact`` always means the reference scan.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Final

from kiro_crew.memory_index.contract import CAPABILITIES, MemoryIndex, MemoryIndexError
from kiro_crew.memory_index.exact import ExactIndex
from kiro_crew.memory_index.local import LocalIndex

IndexFactory = Callable[[], MemoryIndex]

_BUILTINS: Final[dict[str, IndexFactory]] = {
    ExactIndex.name: ExactIndex,
    LocalIndex.name: LocalIndex,
}

_lock = threading.Lock()
_registered: dict[str, IndexFactory] = {}


def register_index_backend(name: str, factory: IndexFactory | None) -> None:
    """Register *factory* under *name*, or remove *name* when *factory* is ``None``."""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise MemoryIndexError("a backend name is a non-empty string with no surrounding spaces")
    if name in _BUILTINS:
        raise MemoryIndexError(f"{name!r} is a built-in memory index backend")
    with _lock:
        if factory is None:
            _registered.pop(name, None)
        else:
            _registered[name] = factory


def backend_names() -> list[str]:
    with _lock:
        return sorted({*_BUILTINS, *_registered})


def create_index_backend(name: str) -> MemoryIndex:
    """A new, unopened backend instance for *name*."""
    with _lock:
        factory = _BUILTINS.get(name) or _registered.get(name)
    if factory is None:
        raise MemoryIndexError(f"no memory index backend named {name!r}")
    index = factory()
    if not isinstance(index, MemoryIndex):
        raise MemoryIndexError(f"backend {name!r} does not implement MemoryIndex")
    unknown = set(index.capabilities) - CAPABILITIES
    if unknown:
        raise MemoryIndexError(f"backend {name!r} declares unknown capabilities {sorted(unknown)}")
    return index
