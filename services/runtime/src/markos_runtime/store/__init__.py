"""Explicit PostgreSQL storage; importing this module opens no connections."""

from markos_runtime.store.database import DurableStore, StorageError

__all__ = ["DurableStore", "StorageError"]
