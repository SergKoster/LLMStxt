"""Анализ сайта перед генерацией: какие файлы рекомендовать и что на сайте стоит поправить.

Все запросы — через probe.fetch: только публичные адреса, редиректы проверяются, ответ
ограничен по размеру. Разбор ответов — в site_checks, он в сеть не ходит.
"""

import hashlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

from app import config, probe, site_checks

log = logging.getLogger("llms.analysis")

# Тексты — черновик агента, утверждает владелец (docs/TASKS.md, 3.1 и 3.3.6).
NO_LLMS = "На сайте нет llms.txt."
LLMS_NOT_STANDARD = "У вас уже есть llms.txt, но он сделан не по стандарту."
LLMS_STANDARD = "У вас уже есть llms.txt, сделанный по стандарту. Новый можно сделать, если хотите."
NO_FULL = "На сайте нет llms-full.txt."
HAS_FULL = "У вас уже есть llms-full.txt. Новый можно сделать, если хотите."
BAD_CERTIFICATE = (
    "У сайта неправильный сертификат https://, поэтому мы не можем безопасно его открыть. "
    "Исправьте сертификат в панели хостинга или укажите адрес с http://."
)
CANT_READ = "Не удалось открыть главную страницу сайта. Проверьте адрес и попробуйте ещё раз."
BUSY = "Сервис сейчас занят. Попробуйте через минуту."

_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_ANALYSES)


class AnalysisError(Exception):
    """Анализ не удался. Текст показывается пользователю."""


class Busy(Exception):
    pass


@dataclass
class FileOffer:
    name: str
    recommended: bool
    reason: str
    about: str


def _fetch(url: str) -> probe.Response | Exception:
    """Запрос, который не бросает исключений: ошибка возвращается вместо ответа."""
    try:
        return probe.fetch(url, timeout=config.ANALYSIS_REQUEST_TIMEOUT_SECONDS)
    except (probe.NoAnswer, probe.Unusable) as exc:
        return exc


def _site_file(answer: probe.Response | Exception) -> bytes | None:
    """Содержимое файла вроде /llms.txt, если он правда есть.

    Многие сайты на любой адрес отдают главную страницу с кодом 200 — это не файл.
    """
    if isinstance(answer, Exception) or answer.status != 200 or not answer.body.strip():
        return None
    if probe.is_html(answer):
        return None
    if len(answer.body) >= probe.MAX_BODY_BYTES:
        # Читаем не больше MAX_BODY_BYTES: последняя строка может быть обрезана — отбрасываем её
        return answer.body[: answer.body.rfind(b"\n") + 1]
    return answer.body


def offer_files(llms_txt: bytes | None, llms_full: bytes | None) -> list[FileOffer]:
    """Правила рекомендаций (ответы владельца 2–4, docs/TASKS.md)."""
    if llms_txt is None:
        llms = FileOffer(config.LLMS_FILE, True, NO_LLMS, "")
    elif site_checks.llms_txt_is_standard(llms_txt):
        llms = FileOffer(config.LLMS_FILE, False, LLMS_STANDARD, "")
    else:
        llms = FileOffer(config.LLMS_FILE, True, LLMS_NOT_STANDARD, "")
    if llms_full is None:
        full = FileOffer(config.FULL_FILE, True, NO_FULL, "")
    else:
        full = FileOffer(config.FULL_FILE, False, HAS_FULL, "")
    offers = [llms, full]
    for offer in offers:
        offer.about = config.FILE_ABOUT[offer.name]
    return offers


def https_state(main_url: str) -> str:
    """Открывается ли сайт по https: ok / none / bad_certificate. Для https-сайта запроса нет."""
    parsed = urlparse(main_url)
    if parsed.scheme == "https":
        return "ok"
    https_url = f"https://{parsed.hostname}/"
    answer = _fetch(https_url)
    if isinstance(answer, probe.BadCertificate):
        return "bad_certificate"
    if isinstance(answer, Exception):
        return "none"
    final = urlparse(answer.url or https_url)
    return "ok" if final.scheme == "https" else "none"  # https переадресует обратно на http


def _open_main(url: str, guess_scheme: bool) -> tuple[str, probe.Response]:
    if guess_scheme:
        picked = probe.pick_scheme(url)
        if picked is None:
            raise AnalysisError(probe.NO_SCHEME)
        url = picked
    answer = _fetch(url)
    if isinstance(answer, probe.NoAnswer):
        raise AnalysisError(probe.NO_ANSWER)
    if isinstance(answer, probe.BadCertificate):
        raise AnalysisError(BAD_CERTIFICATE)
    if isinstance(answer, Exception):
        log.info("Главная %s не открылась: %s", url, answer)
        raise AnalysisError(CANT_READ)
    if answer.status != 200:
        raise AnalysisError(probe.text_for_status(answer.status) or CANT_READ)
    if not probe.is_html(answer):
        raise AnalysisError(probe.NOT_A_PAGE)
    return url, answer


def analyze(url: str, guess_scheme: bool = False) -> dict:
    """Результат для POST /analyze: адрес, предложение файлов и советы. Ошибка — AnalysisError."""
    if not _slots.acquire(blocking=False):
        raise Busy
    try:
        return _analyze(url, guess_scheme)
    finally:
        _slots.release()


def _analyze(url: str, guess_scheme: bool) -> dict:
    url, main_answer = _open_main(url, guess_scheme)
    main_url = main_answer.url or url
    main = site_checks.parse_page(main_url, main_answer.status, main_answer.body)
    links = site_checks.internal_links(main, config.ANALYSIS_MAX_PAGES - 1)

    root = urljoin(main_url, "/")
    missing_page = urljoin(root, f"/llms-check-{uuid.uuid4().hex[:12]}")
    with ThreadPoolExecutor(max_workers=config.ANALYSIS_PARALLEL_REQUESTS) as pool:
        llms_txt = pool.submit(_fetch, urljoin(root, "/llms.txt"))
        llms_full = pool.submit(_fetch, urljoin(root, "/llms-full.txt"))
        robots = pool.submit(_fetch, urljoin(root, "/robots.txt"))
        missing = pool.submit(_fetch, missing_page)
        https = pool.submit(https_state, main_url)
        pages = [pool.submit(_fetch, link) for link in links]

    robots_answer = robots.result()
    robots_txt = None
    if isinstance(robots_answer, probe.Response) and robots_answer.status == 200 and not probe.is_html(robots_answer):
        robots_txt = robots_answer.body.decode("utf-8", errors="ignore")

    missing_answer = missing.result()
    missing_status = missing_answer.status if isinstance(missing_answer, probe.Response) else None

    other_pages = []
    seen_bodies = {hashlib.sha256(main_answer.body).digest()}
    for link, future in zip(links, pages):
        answer = future.result()
        if isinstance(answer, Exception):
            continue  # нет ответа — это не «битая страница», а сбой сети
        if answer.status == 200 and not probe.is_html(answer):
            continue  # ссылка вела на файл
        if answer.status == 200:
            digest = hashlib.sha256(answer.body).digest()
            if digest in seen_bodies:
                continue  # та же страница по другому адресу (/ и /index.html) — не дубль заголовка
            seen_bodies.add(digest)
        other_pages.append(site_checks.parse_page(link, answer.status, answer.body))
    all_pages = [main] + other_pages

    advice = [
        site_checks.check_closed_from_search(main, main_answer.headers, robots_txt),
        site_checks.check_https(https.result()),
        site_checks.check_broken_pages(other_pages),
        site_checks.check_empty_without_js(main_answer.body),
        site_checks.check_viewport(main),
        site_checks.check_titles(all_pages),
        site_checks.check_not_found(missing_status),
    ]
    files = offer_files(_site_file(llms_txt.result()), _site_file(llms_full.result()))
    return {
        "status": "done",
        "url": url,
        "files": [asdict(offer) for offer in files],
        "advice": [asdict(item) for item in advice if item],
        "instruction": config.INSTRUCTION_NAME,  # имя инструкции в архиве — для подписи на странице
    }
