"""Запуск команды llms-gen и перевод её ошибок на понятный язык."""

import logging
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin

from app import config, probe

log = logging.getLogger("llms.generator")

ROBOTS_USER_AGENT = "llms-generator"  # под этим именем llms-gen читает robots.txt


class GenerationError(Exception):
    """Генерация не удалась. Текст ошибки показывается пользователю."""


def find_llms_gen() -> str:
    """Ищет команду llms-gen: сначала рядом с текущим Python (venv), потом в PATH."""
    scripts_dir = Path(sys.executable).parent
    for name in ("llms-gen", "llms-gen.exe"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("llms-gen") or "llms-gen"


def read_crawl_delay(url: str) -> float:
    """Пауза между запросами, которую сайт просит в robots.txt (Crawl-delay). Нет — 0."""
    try:
        with urllib.request.urlopen(urljoin(url, "/robots.txt"), timeout=10) as resp:
            lines = resp.read(500_000).decode("utf-8", errors="ignore").splitlines()
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(lines)
        delay = parser.crawl_delay(ROBOTS_USER_AGENT)
        return float(delay) if delay else 0.0
    except Exception:  # robots.txt нет или он сломан — считаем, что паузы нет
        return 0.0


def pick_max_pages(crawl_delay: float) -> int:
    """Сколько страниц успеем обойти за отведённое время.

    llms-gen сам соблюдает Crawl-delay сайта, а файл пишет только в конце. Если не
    ограничить число страниц, на сайте с большой паузой (habr.com — 10 с) обход не
    уложится в таймаут и пользователь не получит ничего.
    """
    per_page = max(config.REQUEST_DELAY_SECONDS, crawl_delay) + config.AVG_FETCH_SECONDS
    pages = int(config.CRAWL_TIME_BUDGET_SECONDS / per_page)
    return max(config.MIN_PAGES, min(config.MAX_PAGES, pages))


def build_command(url: str, output: Path, max_pages: int, full: bool = False) -> list[str]:
    command = [
        find_llms_gen(),
        url,
        "--depth", str(config.CRAWL_DEPTH),
        "--output", str(output),
        "--no-js",
        "--max-pages", str(max_pages),
        "--delay", str(config.REQUEST_DELAY_SECONDS),
        "--timeout", str(config.REQUEST_TIMEOUT_SECONDS),
    ]
    if full:
        command.append("--full")  # llms-gen положит llms-full.txt рядом с основным файлом
    return command


def fix_broken_entries(text: str) -> str:
    """Склеивает пункты списка, которые llms-gen разорвал на несколько строк.

    Если в заголовке или описании страницы есть перенос строки, llms-gen 0.3.2 пишет его
    как есть, и пункт `- [название](url): описание` разваливается — файл перестаёт быть
    валидным. Здесь каждая строка-хвост приклеивается обратно к своему пункту.
    """
    result: list[str] = []
    in_list = False  # внутри секции со ссылками (после первого "## ")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_list = stripped.startswith("## ")
            result.append(stripped)
        elif not stripped or not in_list or stripped.startswith("- ["):
            result.append(line.rstrip())
        elif result and result[-1].startswith("- ["):
            result[-1] = f"{result[-1]} {stripped}"
        else:
            result.append(line.rstrip())
    return "\n".join(result) + "\n"


def generate(url: str, work_dir: Path, full: bool = False, guess_scheme: bool = False) -> dict[str, Path]:
    """Создаёт в папке work_dir файл llms.txt и, если full, ещё llms-full.txt.

    guess_scheme — схему в url подставили мы, а не пользователь: сначала пробуем https, потом http.
    Возвращает {имя файла: путь}. При неудаче бросает GenerationError;
    папку work_dir убирает тот, кто вызвал.
    """
    if guess_scheme:
        url = probe.pick_scheme(url)
        if url is None:
            raise GenerationError(probe.NO_SCHEME)
    work_dir.mkdir(parents=True, exist_ok=True)
    produced = {config.LLMS_FILE: work_dir / config.LLMS_FILE}
    if full:
        produced[config.FULL_FILE] = work_dir / config.FULL_FILE
    max_pages = pick_max_pages(read_crawl_delay(url))
    command = build_command(url, produced[config.LLMS_FILE], max_pages, full)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.GENERATION_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired:
        raise GenerationError(
            f"Не уложились в {config.GENERATION_TIMEOUT_SECONDS} секунд: "
            "сайт слишком большой или отвечает слишком медленно."
        ) from None
    except OSError as exc:
        log.error("Не удалось запустить llms-gen: %s", exc)
        raise GenerationError("Генератор недоступен. Попробуйте позже.") from None

    missing = [name for name, path in produced.items() if not path.exists() or path.stat().st_size == 0]
    if result.returncode != 0 or missing:
        log.warning("llms-gen завершился с кодом %s для %s: %s", result.returncode, url, result.stderr[-500:])
        if "No pages found" in result.stderr:
            # Только сейчас, после неудачи, спрашиваем сайт сами, чтобы назвать причину.
            raise GenerationError(
                probe.diagnose(url)
                or "Не удалось прочитать страницы сайта. Возможно, сайт недоступен, "
                "запрещает обход или адрес указан неверно."
            )
        raise GenerationError("Не удалось сгенерировать файл для этого сайта.")

    for path in produced.values():
        path.write_text(fix_broken_entries(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return produced
