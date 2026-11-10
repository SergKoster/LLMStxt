"""Удаление старых файлов, чтобы не забить диск."""

import asyncio
import logging
import shutil
import time
from pathlib import Path

from app import config, tasks

log = logging.getLogger("llms.cleanup")


def remove_old_files(directory: Path, max_age: float, now: float | None = None) -> int:
    """Удаляет файлы и забытые рабочие папки старше max_age секунд. Возвращает, сколько удалено."""
    now = time.time() if now is None else now
    removed = 0
    if not directory.exists():
        return 0
    for path in directory.iterdir():
        try:
            if now - path.stat().st_mtime <= max_age:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError as exc:
            log.warning("Не удалось удалить %s: %s", path, exc)
    return removed


async def cleanup_loop() -> None:
    while True:
        removed = await asyncio.to_thread(remove_old_files, config.OUTPUT_DIR, config.FILE_TTL_SECONDS)
        tasks.forget_old_tasks(config.FILE_TTL_SECONDS)
        if removed:
            log.info("Удалено старых файлов: %d", removed)
        await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
