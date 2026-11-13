"""Запуск команды llms-gen и перевод её ошибок на понятный язык."""

import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from xml.sax.saxutils import escape

from app import config, probe

log = logging.getLogger("llms.generator")

ROBOTS_USER_AGENT = "llms-generator"  # под этим именем llms-gen читает robots.txt
MAX_PAGES_WARNING = "Reached --max-pages limit"  # так llms-gen пишет, что обход упёрся в предел

# Адрес страницы в пункте llms.txt: «- [название](адрес)» или «- [название](адрес): описание»
_ENTRY_URL = re.compile(r"^- \[.*?\]\((https?://[^\s)]+)\)")


class GenerationError(Exception):
    """Генерация не удалась. Текст ошибки показывается пользователю."""


class Produced(dict):
    """Готовые файлы {имя: путь}.

    sitemap_partial — сколько страниц в карте сайта, если она неполная (обход упёрся в предел
    страниц). Карта полная или её не просили — None.
    """

    sitemap_partial: int | None = None


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


def _same_site(host_a: str, host_b: str) -> bool:
    strip = lambda host: host.lower().removeprefix("www.")  # noqa: E731
    return strip(host_a) == strip(host_b)


def _encode_url(url: str) -> str:
    """Адрес, как его требует протокол sitemaps.org: домен в punycode, в пути нет кириллицы и пробелов."""
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    safe = "/%:@!$&'()*+,;=-._~"
    return urlunsplit((parts.scheme, netloc, quote(parts.path, safe=safe) or "/", quote(parts.query, safe=safe + "?"), ""))


def sitemap_urls(llms_txt: str, site_url: str) -> list[str]:
    """Адреса для карты сайта из пунктов llms.txt: главная первой, без повторов и чужих сайтов."""
    host = urlsplit(site_url).hostname or ""
    entries = [m.group(1) for line in llms_txt.splitlines() if (m := _ENTRY_URL.match(line.strip()))]
    # Главную берём с той же схемой и доменом, что у прочитанных страниц (после переадресаций)
    candidates = [urljoin(entries[0] if entries else site_url, "/")] + entries
    result, seen = [], set()
    for url in candidates:
        parsed = urlsplit(url)
        if not parsed.hostname or not _same_site(parsed.hostname, host):
            continue
        encoded = _encode_url(url)
        key = encoded.rstrip("/")  # /about и /about/ — одна страница
        if key not in seen:
            seen.add(key)
            result.append(encoded)
    return result


def build_sitemap(urls: list[str]) -> str:
    """sitemap.xml по протоколу sitemaps.org. Дату изменения (lastmod) не пишем: настоящих дат мы не знаем."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    lines += [f"  <url><loc>{escape(url)}</loc></url>" for url in urls]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def generate(
    url: str, work_dir: Path, full: bool = False, guess_scheme: bool = False, sitemap: bool = False
) -> Produced:
    """Создаёт в папке work_dir llms.txt, если full — ещё llms-full.txt, если sitemap — sitemap.xml.

    Карту сайта собираем из адресов страниц, которые llms-gen прочитал для llms.txt: свой
    обход не нужен, а страницы с запретом на поиск (noindex) llms-gen пропускает сам.

    guess_scheme — схему в url подставили мы, а не пользователь: сначала пробуем https, потом http.
    Возвращает Produced {имя файла: путь}. При неудаче бросает GenerationError;
    папку work_dir убирает тот, кто вызвал.
    """
    if guess_scheme:
        url = probe.pick_scheme(url)
        if url is None:
            raise GenerationError(probe.NO_SCHEME)
    work_dir.mkdir(parents=True, exist_ok=True)
    produced = Produced({config.LLMS_FILE: work_dir / config.LLMS_FILE})
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
    if sitemap:
        urls = sitemap_urls(produced[config.LLMS_FILE].read_text(encoding="utf-8"), url)
        produced[config.SITEMAP_FILE] = work_dir / config.SITEMAP_FILE
        produced[config.SITEMAP_FILE].write_text(build_sitemap(urls), encoding="utf-8", newline="\n")
        if MAX_PAGES_WARNING in result.stderr:
            produced.sitemap_partial = len(urls)
    return produced
