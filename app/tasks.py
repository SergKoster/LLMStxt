"""Хранилище задач в памяти и фоновая функция генерации."""

import logging
import os
import shutil
import threading
import time
import uuid
import zipfile
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
    sitemap_partial: int | None = None  # карта сайта неполная: сколько в ней страниц
    created_at: float = field(default_factory=time.time)


_tasks: dict[str, Task] = {}
_lock = threading.Lock()
_slots = threading.Semaphore(config.MAX_CONCURRENT_GENERATIONS)


class TooManyTasks(Exception):
    pass


# Готовый результат задачи — всегда один файл в OUTPUT_DIR. Его имя говорит, что внутри.
_RESULTS = (
    (".txt", config.LLMS_FILE),
    (".full.txt", config.FULL_FILE),
    (".sitemap.xml", config.SITEMAP_FILE),
    (".zip", config.ARCHIVE_NAME),
)
_SUFFIX_BY_FILE = {name: suffix for suffix, name in _RESULTS}


def find_result(task_id: str) -> tuple[Path, str] | None:
    """Готовый файл задачи и имя, под которым его скачает пользователь."""
    for suffix, download_name in _RESULTS:
        path = config.OUTPUT_DIR / f"{task_id}{suffix}"
        if path.is_file():
            return path, download_name
    return None


def instruction_text(files: list[str], sitemap_partial: int | None = None) -> str:
    """Файл-инструкция для архива: что за файлы внутри и что с ними делать.

    Переносы строк CRLF — чтобы старый Блокнот Windows не склеил текст в одну строку.
    """
    parts = []
    for name in files:
        about = config.FILE_ABOUT[name]
        if name == config.SITEMAP_FILE and sitemap_partial:
            about += " " + config.SITEMAP_PARTIAL_NOTE.format(pages=sitemap_partial)
        parts.append(f"{name}\r\n{about}")
    return "\r\n\r\n".join(parts) + "\r\n"


def _pack(task_id: str, produced: dict[str, Path], files: list[str], sitemap_partial: int | None = None) -> None:
    """Один выбранный файл отдаём как есть, несколько — одним zip-архивом."""
    if len(files) == 1:
        os.replace(produced[files[0]], config.OUTPUT_DIR / f"{task_id}{_SUFFIX_BY_FILE[files[0]]}")
        return
    archive = config.OUTPUT_DIR / f"{task_id}.zip"
    part = archive.with_name(archive.name + ".part")  # чтобы не отдать недописанный архив
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.write(produced[name], arcname=name)
        zf.writestr(config.INSTRUCTION_NAME, instruction_text(files, sitemap_partial))
    os.replace(part, archive)


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


def _finish(task_id: str, status: str, error: str = "", sitemap_partial: int | None = None) -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.status = status
            task.error = error
            task.sitemap_partial = sitemap_partial


def run_task(task_id: str, url: str, files: list[str], guess_scheme: bool = False) -> None:
    """Фоновая функция: запускается через BackgroundTasks в отдельном потоке."""
    work_dir = config.OUTPUT_DIR / f"{task_id}.work"
    with _slots:
        try:
            want_sitemap = config.SITEMAP_FILE in files
            produced = generate(
                url, work_dir, full=config.FULL_FILE in files, guess_scheme=guess_scheme, sitemap=want_sitemap
            )
            sitemap_partial = produced.sitemap_partial if want_sitemap else None
            _pack(task_id, produced, files, sitemap_partial)
        except GenerationError as exc:
            _finish(task_id, ERROR, str(exc))
        except Exception:
            log.exception("Неожиданная ошибка в задаче %s", task_id)
            _finish(task_id, ERROR, "Внутренняя ошибка сервиса. Попробуйте ещё раз.")
        else:
            _finish(task_id, DONE, sitemap_partial=sitemap_partial)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


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
