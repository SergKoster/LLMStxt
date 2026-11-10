"""Все числа и пути сервиса в одном месте."""

import os
import tempfile
from pathlib import Path

# Куда складываются готовые файлы. В Docker это /tmp/llms_outputs (как в задании).
OUTPUT_DIR = Path(os.getenv("LLMS_OUTPUT_DIR", Path(tempfile.gettempdir()) / "llms_outputs"))

# --- Требования из задания ---
GENERATION_TIMEOUT_SECONDS = 180  # жёсткий предел на одну генерацию
CRAWL_DEPTH = 3  # --depth
FILE_TTL_SECONDS = 60 * 60  # файлы старше часа удаляются

# --- Наши лимиты, чтобы уложиться в «около минуты» ---
# llms-gen пишет файл только в самом конце, поэтому число страниц ограничиваем заранее.
CRAWL_TIME_BUDGET_SECONDS = 60  # сколько примерно должен длиться обход
MAX_PAGES = 50  # верхний предел страниц в файле
MIN_PAGES = 3
REQUEST_DELAY_SECONDS = 0.3  # --delay: пауза между запросами к сайту
REQUEST_TIMEOUT_SECONDS = 15  # --timeout: ожидание одной страницы
AVG_FETCH_SECONDS = 1.0  # грубая оценка времени загрузки одной страницы

# --- Защита сервера ---
MAX_URL_LENGTH = 2000
MAX_CONCURRENT_GENERATIONS = 2  # сколько llms-gen работают одновременно
MAX_ACTIVE_TASKS = 10  # сколько задач может ждать и выполняться; дальше — отказ
CLEANUP_INTERVAL_SECONDS = 5 * 60
