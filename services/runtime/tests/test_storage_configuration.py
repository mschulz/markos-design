import asyncio

import pytest

from markos_runtime.store import DurableStore, StorageError


@pytest.mark.parametrize("dsn", ["", "user=implicit", "PRIVATE_NOT_A_DSN"])
def test_storage_configuration_errors_are_redacted(dsn: str) -> None:
    async def scenario() -> None:
        with pytest.raises(StorageError) as error:
            async with DurableStore.connect(dsn):
                pass
        assert "PRIVATE_NOT_A_DSN" not in str(error.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("budget", [0, 5, True])
def test_pool_budget_is_bounded(budget: int) -> None:
    async def scenario() -> None:
        with pytest.raises(StorageError):
            async with DurableStore.connect("", max_connections=budget):
                pass

    asyncio.run(scenario())


def test_background_pool_connection_errors_are_redacted() -> None:
    import psycopg

    from markos_runtime.store.database import _SafeConnection

    async def scenario() -> None:
        with pytest.raises(psycopg.OperationalError) as error:
            await _SafeConnection.connect("PRIVATE_INVALID_CONNECTION_STRING")
        assert str(error.value) == "database connection unavailable"
        assert error.value.__suppress_context__

    asyncio.run(scenario())
