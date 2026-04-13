"""Tests for the background task scheduler (Item 41).

Tests cover:
- Task seeding — default tasks created on startup
- Task execution — run_task_now executes and records results
- Task enable/disable — toggle works
- Scheduler loop — due tasks are found and executed
- Admin chat tools — list, run, toggle, update
"""

import pytest
from datetime import datetime, timedelta, UTC
from sqlalchemy import delete, select

from farmafacil.db.session import async_session
from farmafacil.models.database import ScheduledTask
from farmafacil.services.scheduler import (
    DEFAULT_TASKS,
    TASK_REGISTRY,
    _check_and_run_due_tasks,
    run_task_now,
    seed_scheduled_tasks,
)


@pytest.fixture(autouse=True)
async def _clean_tasks():
    """Clean scheduled_tasks before each test."""
    async with async_session() as session:
        await session.execute(delete(ScheduledTask))
        await session.commit()


class TestTaskSeeding:
    """Verify default tasks are seeded correctly."""

    @pytest.mark.asyncio
    async def test_seed_creates_default_tasks(self):
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(select(ScheduledTask))
            tasks = result.scalars().all()

        assert len(tasks) == len(DEFAULT_TASKS)
        task_keys = {t.task_key for t in tasks}
        for _, key, _, _ in DEFAULT_TASKS:
            assert key in task_keys

    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self):
        await seed_scheduled_tasks()
        await seed_scheduled_tasks()  # second call

        async with async_session() as session:
            result = await session.execute(select(ScheduledTask))
            tasks = result.scalars().all()

        assert len(tasks) == len(DEFAULT_TASKS)

    @pytest.mark.asyncio
    async def test_seeded_tasks_enabled_by_default(self):
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(select(ScheduledTask))
            tasks = result.scalars().all()

        for task in tasks:
            assert task.enabled is True
            assert task.status == "idle"

    @pytest.mark.asyncio
    async def test_seeded_tasks_have_next_run(self):
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(select(ScheduledTask))
            tasks = result.scalars().all()

        for task in tasks:
            assert task.next_run_at is not None


class TestTaskRegistry:
    """Verify the task registry is properly configured."""

    def test_all_default_task_keys_in_registry(self):
        for _, key, _, _ in DEFAULT_TASKS:
            assert key in TASK_REGISTRY, f"Task key {key!r} not in TASK_REGISTRY"

    def test_registry_functions_are_callable(self):
        for key, func in TASK_REGISTRY.items():
            assert callable(func), f"TASK_REGISTRY[{key!r}] is not callable"


class TestRunTaskNow:
    """Verify manual task execution."""

    @pytest.mark.asyncio
    async def test_run_rescore_products(self):
        """rescore_products runs successfully."""
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()

        result_msg = await run_task_now(task.id)
        assert "products" in result_msg.lower() or "classified" in result_msg.lower() or "unclassified" in result_msg.lower()

        # Verify status updated
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
            refreshed = result.scalar_one()

        assert refreshed.status == "success"
        assert refreshed.last_run_at is not None
        assert refreshed.last_result is not None
        assert refreshed.last_duration_seconds is not None
        assert refreshed.last_duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_cleanup_stale_cache(self):
        """cleanup_stale_cache runs successfully."""
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "cleanup_stale_cache"
                )
            )
            task = result.scalar_one()

        result_msg = await run_task_now(task.id)
        assert "deleted" in result_msg.lower() or "stale" in result_msg.lower()

    @pytest.mark.asyncio
    async def test_run_nonexistent_task(self):
        result_msg = await run_task_now(99999)
        assert "not found" in result_msg.lower()

    @pytest.mark.asyncio
    async def test_run_updates_next_run_at(self):
        """After running, next_run_at is set to now + interval."""
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task_id = task.id
            interval = task.interval_minutes

        await run_task_now(task_id)

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()

        assert refreshed.next_run_at is not None
        # next_run should be roughly now + interval (within 60s tolerance)
        expected = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(minutes=interval)
        delta = abs((refreshed.next_run_at - expected).total_seconds())
        assert delta < 60, f"next_run_at off by {delta}s"


class TestSchedulerLoop:
    """Verify the scheduler loop finds and runs due tasks."""

    @pytest.mark.asyncio
    async def test_due_task_is_executed(self):
        """A task with next_run_at in the past gets executed."""
        await seed_scheduled_tasks()

        # Set one task to be due
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task.next_run_at = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(minutes=5)
            await session.commit()
            task_id = task.id

        await _check_and_run_due_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()

        assert refreshed.status == "success"
        assert refreshed.last_run_at is not None

    @pytest.mark.asyncio
    async def test_disabled_task_is_skipped(self):
        """A disabled task is not executed even if due."""
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task.enabled = False
            task.next_run_at = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(minutes=5)
            await session.commit()
            task_id = task.id

        await _check_and_run_due_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()

        assert refreshed.status == "idle"
        assert refreshed.last_run_at is None

    @pytest.mark.asyncio
    async def test_future_task_is_not_executed(self):
        """A task with next_run_at in the future is not executed."""
        await seed_scheduled_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task.next_run_at = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=1)
            await session.commit()
            task_id = task.id

        await _check_and_run_due_tasks()

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()

        assert refreshed.status == "idle"


class TestAdminChatTools:
    """Verify admin chat tools for scheduler."""

    @pytest.mark.asyncio
    async def test_list_scheduled_tasks(self):
        from farmafacil.services.admin_chat import _tool_list_scheduled_tasks

        await seed_scheduled_tasks()
        result = await _tool_list_scheduled_tasks({})
        assert "Cleanup stale search cache" in result
        assert "Refresh store locations" in result

    @pytest.mark.asyncio
    async def test_run_scheduled_task(self):
        from farmafacil.services.admin_chat import _tool_run_scheduled_task

        await seed_scheduled_tasks()
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()

        result_msg = await _tool_run_scheduled_task({"task_id": task.id})
        assert "ejecutada" in result_msg.lower()

    @pytest.mark.asyncio
    async def test_toggle_scheduled_task(self):
        from farmafacil.services.admin_chat import _tool_toggle_scheduled_task

        await seed_scheduled_tasks()
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task_id = task.id

        result_msg = await _tool_toggle_scheduled_task({"task_id": task_id, "enabled": False})
        assert "pausada" in result_msg

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()
        assert refreshed.enabled is False

    @pytest.mark.asyncio
    async def test_update_scheduled_task_interval(self):
        from farmafacil.services.admin_chat import _tool_update_scheduled_task

        await seed_scheduled_tasks()
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.task_key == "rescore_products"
                )
            )
            task = result.scalar_one()
            task_id = task.id

        result_msg = await _tool_update_scheduled_task({"task_id": task_id, "interval_minutes": 120})
        assert "120" in result_msg

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
            refreshed = result.scalar_one()
        assert refreshed.interval_minutes == 120
