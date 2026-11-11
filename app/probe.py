"""Проверочные запросы к сайту: какую схему взять и почему обход не удался.

Пользователю показываются только тексты из этого файла, а не ответ сайта и не вывод llms-gen.
"""

import logging
import socket
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from app import config
from app.validation import UrlError, check_public_host

log = logging.getLogger("llms.probe")

try:  # тот же User-Agent, что у llms-gen: сайт должен ответить нам так же, как ему
    from llms_generator.crawler import ROBOTS_UA, USER_AGENT
except ImportError:  # pragma: no cover
    USER_AGENT, ROBOTS_UA = "llms-generator", "llms-generator"

MAX_BODY_BYTES = 256_000

# --- Тексты ошибок (утверждены владельцем, см. docs/TASKS.md) ---
BLOCKED = "Сайт защищён от автоматических программ и не отдаёт нам свои страницы. Сделать файл для этого сайта не получится."
ROBOTS = "Владелец сайта запретил автоматический обход в файле robots.txt. Мы соблюдаем этот запрет, поэтому файл сделать нельзя."
AUTH = "Сайт открывается только после входа по логину и паролю. Такие страницы мы прочитать не можем."
NOT_FOUND = "По этому адресу страницы нет. Проверьте адрес."
SERVER_ERROR = "Сайт сейчас работает с ошибкой. Попробуйте позже."
NO_ANSWER = "Сайт не ответил на запрос. Возможно, он временно недоступен. Попробуйте позже."
NO_SCHEME = "Не получилось открыть сайт ни по https://, ни по http://. Проверьте адрес."
NOT_A_PAGE = "По этому адресу находится не страница сайта, а файл. Укажите адрес страницы."
NO_TEXT = (
    "Сайт открылся, но текста на страницах нет: скорее всего, он появляется только после работы "
    "скриптов в браузере. Такой сайт мы прочитать не можем."
)


class NoAnswer(Exception):
    """Сайт не ответил вовремя или соединение не установилось."""


class Unusable(Exception):
    """Ответ получить нельзя по другой причине: редирект во внутреннюю сеть, ошибка TLS и т.п."""


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Редирект разрешён только на http/https и только на хост из интернета."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(urljoin(req.full_url, newurl))
        if target.scheme not in ("http", "https") or not target.hostname:
            raise Unusable(f"редирект на {target.scheme}")
        try:
            check_public_host(target.hostname, target.port)
        except UrlError:
            raise Unusable("редирект во внутреннюю сеть или на несуществующий хост") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def fetch(url: str, user_agent: str = USER_AGENT) -> Response:
    """GET-запрос. Любой HTTP-ответ (и 403, и 500) возвращается; без ответа — NoAnswer/Unusable."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with _opener.open(request, timeout=config.REQUEST_TIMEOUT_SECONDS) as resp:
            return Response(resp.status, resp.headers.get("Content-Type", ""), resp.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:
        with exc:
            return Response(exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b"")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionError)):
            raise NoAnswer(str(exc.reason)) from None
        raise Unusable(str(exc.reason)) from None
    except (TimeoutError, ConnectionError) as exc:
        raise NoAnswer(str(exc)) from None
    except OSError as exc:  # обрыв посреди ответа и прочие сетевые сбои
        raise Unusable(str(exc)) from None


def pick_scheme(url: str) -> str | None:
    """Для адреса, где схему подставили мы: https, если сайт по нему отвечает, иначе http.

    «Отвечает» — любой HTTP-ответ, даже ошибка: значит, сайт по https есть.
    Не открылся ни так, ни так — None.
    """
    rest = url.split("://", 1)[1]
    for scheme in ("https", "http"):
        candidate = f"{scheme}://{rest}"
        try:
            fetch(candidate)
            return candidate
        except (NoAnswer, Unusable) as exc:
            log.info("%s не открылся: %s", candidate, exc)
    return None


def _robots_forbid(url: str) -> bool:
    """robots.txt запрещает llms-gen эту страницу. Решаем так же, как llms-gen 0.3.2:
    нет файла, он недоступен или в нём только правила для `*` — запрета нет."""
    try:
        resp = fetch(urljoin(url, "/robots.txt"), user_agent="Python-urllib")
    except (NoAnswer, Unusable):
        return False
    if resp.status != 200:
        return False
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(resp.body.decode("utf-8", errors="ignore").splitlines())
    return bool(parser.entries) and not parser.can_fetch(ROBOTS_UA, url)


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<html" in head


def diagnose(url: str) -> str | None:
    """Понятная причина, почему llms-gen не нашёл ни одной страницы. Не ясно — None."""
    try:
        resp = fetch(url)
    except NoAnswer:
        return NO_ANSWER
    except Unusable as exc:
        log.info("Проверка %s не дала ответа: %s", url, exc)
        return None

    if resp.status == 401:
        return AUTH
    if resp.status in (403, 429):
        return BLOCKED
    if resp.status in (404, 410):
        return NOT_FOUND
    if resp.status >= 500:
        return SERVER_ERROR
    if resp.status != 200:
        return None
    if "text/html" not in resp.content_type.lower() and not _looks_like_html(resp.body):
        return NOT_A_PAGE
    if _robots_forbid(url):
        return ROBOTS
    if not resp.body.strip():
        return NO_TEXT
    return None
