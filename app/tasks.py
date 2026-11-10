"""Хранилище задач в памяти и фоновая функция генерации."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app import config
from app.generator import GenerationError, generate

log = logging.getLogger("llms.tasks")

PROCESSING = "processing"
DONE = "done"
ERROR = "error"


@dataclass
class Task:
    status: str = PROCESSING
    error: str = ""
    created_at: float = field(default_factory=time.time)


_tasks: dict[str, Task] = {}
_lock = threading.Lock()
_slots = threading.Semaphore(config.MAX_CONCURRENT_GENERATIONS)


class TooManyTasks(Exception):
    pass


def output_path(task_id: str) -> Path:
    return config.OUTPUT_DIR / f"{task_id}.txt"


def create_task() -> str:
    with _lock:
        active = sum(1 for task in _tasks.values() if task.status == PROCESSING)
        if active >= config.MAX_ACTIVE_TASKS:
            raise TooManyTasks
        task_id = str(uuid.uuid4())
        _tasks[task_id] = Task()
        return task_id


def get_task(task_id: str) -> Task | None:
    with _lock:
        return _tasks.get(task_id)


def _finish(task_id: str, status: str, error: str = "") -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.status = status
            task.error = error


def run_task(task_id: str, url: str) -> None:
    """Фоновая функция: запускается через BackgroundTasks в отдельном потоке."""
    with _slots:
        try:
            generate(url, output_path(task_id))
        except GenerationError as exc:
            _finish(task_id, ERROR, str(exc))
        except Exception:
            log.exception("Неожиданная ошибка в задаче %s", task_id)
            _finish(task_id, ERROR, "Внутренняя ошибка сервиса. Попробуйте ещё раз.")
        else:
            _finish(task_id, DONE)


def forget_old_tasks(max_age: float, now: float | None = None) -> int:
    """Удаляет из памяти завершённые задачи старше max_age секунд."""
    now = time.time() if now is None else now
    with _lock:
        old = [
            task_id
            for task_id, task in _tasks.items()
            if task.status != PROCESSING and now - task.created_at > max_age
        ]
        for task_id in old:
            del _tasks[task_id]
    return len(old)
