"""Story 1.2: the reusable storage tests, run against the demonstration store and another store."""

from __future__ import annotations

import unittest

from assistant.store.interface import Store
from assistant.store.sqlite import SqliteStore
from contract.store import StoreContract
from tests.memory_store import MemoryStore


class DemoStorePassesStorageContractTest(StoreContract, unittest.IsolatedAsyncioTestCase):
    """Story 1.2 - demo store passes storage tests."""

    async def make_store(self) -> Store:
        return SqliteStore()


class OtherStorePassesStorageContractTest(StoreContract, unittest.IsolatedAsyncioTestCase):
    """Story 1.2 - storage tests are store-agnostic: the same suite, unchanged."""

    async def make_store(self) -> Store:
        return MemoryStore()


if __name__ == "__main__":
    unittest.main()
