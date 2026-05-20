"""Background task scheduler — runs maintenance tasks on a configurable interval.

Tasks are stored in the ``scheduled_tasks`` DB table and managed via SQLAdmin.
The scheduler loop runs as an ``asyncio.Task`` inside the FastAPI lifespan,
checking for due tasks every 60 seconds.

Task functions are registered in ``TASK_REGISTRY`` — admins can enable/disable
and change intervals from the UI, but cannot define arbitrary code.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import ScheduledTask

logger = logging.getLogger(__name__)

# How often the scheduler checks for due tasks (seconds).
POLL_INTERVAL = 60

# ── Task functions ────────────────────────────────────────────────────────
# Each must be ``async def(task: ScheduledTask) -> str`` returning a
# short result message.  The scheduler catches exceptions and writes
# them to ``last_result``.

TaskFunc = Callable[[ScheduledTask], Awaitable[str]]


async def _cleanup_stale_cache(task: ScheduledTask) -> str:
    """Delete search_queries older than the cache TTL."""
    from farmafacil.services.settings import get_setting_int

    ttl_minutes = await get_setting_int("cache_ttl_minutes")
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=ttl_minutes)

    from sqlalchemy import delete as sa_delete

    from farmafacil.models.database import SearchQuery

    async with async_session() as session:
        # SearchQuery.searched_at is naive (no tz), so strip tz for comparison
        cutoff_naive = cutoff.replace(tzinfo=None)
        result = await session.execute(
            sa_delete(SearchQuery).where(SearchQuery.searched_at < cutoff_naive)
        )
        deleted = result.rowcount
        await session.commit()

    return f"Deleted {deleted} stale cache entries (TTL={ttl_minutes}min)"


async def _backfill_stores(task: ScheduledTask) -> str:
    """Refresh store locations from pharmacy APIs."""
    from farmafacil.services.store_backfill import backfill_stores

    await backfill_stores()
    return "Store backfill complete"


async def _rescore_products(task: ScheduledTask) -> str:
    """Re-classify is_pharmaceutical on ALL products with a drug_class.

    Runs ``classify_pharmaceutical`` against the current
    ``NON_PHARMA_CATEGORIES`` set, so code changes (adding/removing
    categories) are applied to existing products on the next scheduler
    tick without manual SQL.  Only updates rows whose classification
    actually changed.
    """
    from farmafacil.models.database import Product
    from farmafacil.services.relevance import classify_pharmaceutical

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.drug_class.is_not(None))
        )
        products = result.scalars().all()
        if not products:
            return "No products with drug_class"

        changed = 0
        for product in products:
            new_value = classify_pharmaceutical(product.drug_class)
            if product.is_pharmaceutical != new_value:
                product.is_pharmaceutical = new_value
                changed += 1

        if changed:
            await session.commit()

    return f"Checked {len(products)} products, reclassified {changed}"


async def _osm_backfill(task: ScheduledTask) -> str:
    """Item 46 — pull pharmacies from OpenStreetMap, dedupe, insert/update."""
    from farmafacil.services.osm_backfill import backfill_from_osm

    summary = await backfill_from_osm()
    return (
        f"OSM backfill: +{summary['inserted']} new, "
        f"~{summary['updated']} updated, ={summary['skipped']} unchanged, "
        f"x{summary['rejected']} rejected"
    )


async def _zone_backfill(task: ScheduledTask) -> str:
    """Item 45 — reverse-geocode pharmacy_locations rows missing zone_name."""
    from farmafacil.services.store_backfill import backfill_zone_names

    summary = await backfill_zone_names()
    return (
        f"Zone backfill: processed {summary['processed']}, "
        f"updated {summary['updated']}, failed {summary['failed']}"
    )


async def _geocode_cache_cleanup(task: ScheduledTask) -> str:
    """Item 47 — drop expired geocode_cache rows."""
    from farmafacil.services.location import cleanup_expired_cache

    deleted = await cleanup_expired_cache(older_than_days=90)
    return f"Pruned {deleted} expired geocode_cache rows"


async def _cleanup_old_logs(task: ScheduledTask) -> str:
    """Delete conversation_logs older than 90 days."""
    from sqlalchemy import delete as sa_delete

    from farmafacil.models.database import ConversationLog

    cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    cutoff_naive = cutoff.replace(tzinfo=None)

    async with async_session() as session:
        result = await session.execute(
            sa_delete(ConversationLog).where(
                ConversationLog.created_at < cutoff_naive
            )
        )
        deleted = result.rowcount
        await session.commit()

    return f"Deleted {deleted} conversation logs older than 90 days"


async def _cleanup_old_voice_messages(task: ScheduledTask) -> str:
    """Delete voice_messages and their audio files older than 90 days.

    Steps:
      1. SELECT rows older than cutoff to get audio_path values.
      2. Delete the physical OGG files from disk (tolerates missing files).
      3. DELETE the DB rows (FKs in search_logs, user_feedback,
         user_suggestions are ON DELETE SET NULL — handled by the DB).
      4. Remove empty user audio subdirectories left behind.
    """
    import os
    from pathlib import Path

    from sqlalchemy import delete as sa_delete

    from farmafacil.models.database import VoiceMessage
    import farmafacil.services.voice as _voice_mod

    cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    cutoff_naive = cutoff.replace(tzinfo=None)

    # Step 1: gather audio paths before deletion.
    async with async_session() as session:
        result = await session.execute(
            select(VoiceMessage.audio_path).where(
                VoiceMessage.created_at < cutoff_naive
            )
        )
        audio_paths: list[str] = [row[0] for row in result.fetchall()]

    # Step 2: remove audio files from disk.
    files_deleted = 0
    parent_dirs: set[Path] = set()
    audio_base = _voice_mod.AUDIO_BASE_DIR
    for rel_path in audio_paths:
        abs_path = audio_base.parent / rel_path
        try:
            if abs_path.is_file():
                os.remove(abs_path)
                files_deleted += 1
                parent_dirs.add(abs_path.parent)
        except OSError:
            logger.warning("Could not delete audio file: %s", abs_path)

    # Step 3: delete DB rows.
    async with async_session() as session:
        result = await session.execute(
            sa_delete(VoiceMessage).where(
                VoiceMessage.created_at < cutoff_naive
            )
        )
        rows_deleted = result.rowcount
        await session.commit()

    # Step 4: clean up empty user subdirectories (best-effort).
    for d in parent_dirs:
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass  # non-critical — dir still in use or already gone

    return (
        f"Deleted {rows_deleted} voice messages + "
        f"{files_deleted} audio files older than 90 days"
    )


# ── Registry ──────────────────────────────────────────────────────────────

TASK_REGISTRY: dict[str, TaskFunc] = {
    "cleanup_stale_cache": _cleanup_stale_cache,
    "backfill_stores": _backfill_stores,
    "rescore_products": _rescore_products,
    "cleanup_old_logs": _cleanup_old_logs,
    "cleanup_old_voice_messages": _cleanup_old_voice_messages,
    "osm_backfill": _osm_backfill,
    "zone_backfill": _zone_backfill,
    "geocode_cache_cleanup": _geocode_cache_cleanup,
}

# Default tasks seeded on first startup.
# Format: (name, task_key, interval_minutes, enabled)
DEFAULT_TASKS: list[tuple[str, str, int, bool]] = [
    ("Cleanup stale search cache", "cleanup_stale_cache", 60, True),
    ("Refresh store locations", "backfill_stores", 1440, True),
    ("Re-score product categories", "rescore_products", 1440, True),
    ("Cleanup old conversation logs", "cleanup_old_logs", 10080, True),
    # v0.18.0 Item 46 — monthly because Overpass API has no urgency
    ("OSM pharmacy backfill", "osm_backfill", 43200, True),
    # v0.18.0 Item 45 — daily because Nominatim is rate-limited at 1 req/sec
    ("Pharmacy zone backfill", "zone_backfill", 1440, True),
    # v0.19.0 Item 47 — weekly is fine; cache rows do no harm just sitting there
    ("Geocode cache cleanup", "geocode_cache_cleanup", 10080, True),
    # v0.22.6 — weekly; deletes voice_messages + audio files older than 90 days
    ("Cleanup old voice messages", "cleanup_old_voice_messages", 10080, True),
]


# ── Seed ──────────────────────────────────────────────────────────────────


async def seed_scheduled_tasks() -> None:
    """Insert default scheduled tasks if they don't exist."""
    async with async_session() as session:
        for name, task_key, interval, enabled in DEFAULT_TASKS:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.task_key == task_key)
            )
            if result.scalar_one_or_none() is None:
                now = datetime.now(tz=UTC).replace(tzinfo=None)
                session.add(ScheduledTask(
                    name=name,
                    task_key=task_key,
                    interval_minutes=interval,
                    enabled=enabled,
                    next_run_at=now,  # run immediately on first startup
                    status="idle",
                ))
        await session.commit()
    logger.info("Scheduled tasks seeded")


# ── Runner ────────────────────────────────────────────────────────────────


async def run_task_now(task_id: int) -> str:
    """Manually trigger a task by ID.  Returns the result message.

    Used by the SQLAdmin "Run Now" action.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(ScheduledTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return f"Task {task_id} not found"

    return await _execute_task(task)


async def _execute_task(task: ScheduledTask) -> str:
    """Execute a single task: update status, run, record result."""
    func = TASK_REGISTRY.get(task.task_key)
    if func is None:
        msg = f"Unknown task_key: {task.task_key}"
        logger.error(msg)
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
            db_task = result.scalar_one()
            db_task.status = "failed"
            db_task.last_result = msg
            await session.commit()
        return msg

    # Mark as running
    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(ScheduledTask.id == task.id)
        )
        db_task = result.scalar_one()
        db_task.status = "running"
        await session.commit()

    # Maximum seconds a single task run may take before it is cancelled.
    # Prevents a stuck task from blocking the scheduler loop indefinitely.
    # (Item 79, v0.25.0)
    TASK_TIMEOUT = 300  # 5 minutes

    start = time.monotonic()
    try:
        result_msg = await asyncio.wait_for(func(task), timeout=TASK_TIMEOUT)
        elapsed = time.monotonic() - start

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
            db_task = result.scalar_one()
            now = datetime.now(tz=UTC).replace(tzinfo=None)
            db_task.status = "success"
            db_task.last_result = result_msg
            db_task.last_run_at = now
            db_task.last_duration_seconds = round(elapsed, 2)
            db_task.next_run_at = now + timedelta(minutes=db_task.interval_minutes)
            await session.commit()

        logger.info(
            "Task '%s' completed in %.1fs: %s", task.name, elapsed, result_msg,
        )
        return result_msg

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        timeout_msg = (
            f"Task timed out after {TASK_TIMEOUT}s "
            f"(elapsed: {elapsed:.1f}s)"
        )
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
            db_task = result.scalar_one()
            now = datetime.now(tz=UTC).replace(tzinfo=None)
            db_task.status = "timeout"
            db_task.last_result = timeout_msg
            db_task.last_run_at = now
            db_task.last_duration_seconds = round(elapsed, 2)
            db_task.next_run_at = now + timedelta(minutes=db_task.interval_minutes)
            await session.commit()

        logger.error(
            "Task '%s' timed out after %.1fs (limit=%ds)",
            task.name, elapsed, TASK_TIMEOUT,
        )
        return timeout_msg

    except Exception as exc:
        elapsed = time.monotonic() - start
        error_msg = f"{type(exc).__name__}: {exc}"

        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
            db_task = result.scalar_one()
            now = datetime.now(tz=UTC).replace(tzinfo=None)
            db_task.status = "failed"
            db_task.last_result = error_msg
            db_task.last_run_at = now
            db_task.last_duration_seconds = round(elapsed, 2)
            db_task.next_run_at = now + timedelta(minutes=db_task.interval_minutes)
            await session.commit()

        logger.error(
            "Task '%s' failed after %.1fs: %s",
            task.name, elapsed, error_msg, exc_info=exc,
        )
        return error_msg


# ── Background loop ──────────────────────────────────────────────────────


async def scheduler_loop() -> None:
    """Poll for due tasks and execute them.  Runs forever as a background task."""
    logger.info("Scheduler loop started (poll every %ds)", POLL_INTERVAL)

    while True:
        try:
            await _check_and_run_due_tasks()
        except Exception:
            logger.error("Scheduler loop error", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


async def _check_and_run_due_tasks() -> None:
    """Find enabled tasks that are due and execute them."""
    now = datetime.now(tz=UTC).replace(tzinfo=None)

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.status != "running",
                ScheduledTask.next_run_at <= now,
            )
        )
        due_tasks = result.scalars().all()

    for task in due_tasks:
        logger.info("Running scheduled task: %s", task.name)
        await _execute_task(task)
