"""Проверочные запросы к сайту: какую схему взять и почему обход не удался.

Пользователю показываются только тексты из этого файла, а не ответ сайта и не вывод llms-gen.
"""

import logging
import socket
import ssl
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from app import config, site_checks
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


class BadCertificate(Unusable):
    """Сайт отвечает по https, но его сертификат не проходит проверку."""


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)  # имена в нижнем регистре
    url: str = ""  # адрес после всех редиректов


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


def _headers(message) -> dict[str, str]:
    return {name.lower(): value for name, value in message.items()} if message else {}


def fetch(url: str, user_agent: str = USER_AGENT, timeout: float | None = None) -> Response:
    """GET-запрос. Любой HTTP-ответ (и 403, и 500) возвращается; без ответа — NoAnswer/Unusable."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    timeout = timeout or config.REQUEST_TIMEOUT_SECONDS
    try:
        with _opener.open(request, timeout=timeout) as resp:
            headers = _headers(resp.headers)
            body = resp.read(MAX_BODY_BYTES)
            return Response(resp.status, headers.get("content-type", ""), body, headers, resp.url)
    except urllib.error.HTTPError as exc:
        with exc:
            headers = _headers(exc.headers)
            return Response(exc.code, headers.get("content-type", ""), b"", headers, exc.url or url)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionError)):
            raise NoAnswer(str(exc.reason)) from None
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise BadCertificate(str(exc.reason)) from None
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


def looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<html" in head


def is_html(resp: Response) -> bool:
    return "text/html" in resp.content_type.lower() or looks_like_html(resp.body)


def text_for_status(status: int) -> str | None:
    """Готовый текст для ответа сайта с ошибкой. 200 и непонятные коды — None."""
    if status == 401:
        return AUTH
    if status in (403, 429):
        return BLOCKED
    if status in (404, 410):
        return NOT_FOUND
    if status >= 500:
        return SERVER_ERROR
    return None


def diagnose(url: str) -> str | None:
    """Понятная причина, почему llms-gen не нашёл ни одной страницы. Не ясно — None."""
    try:
        resp = fetch(url)
    except NoAnswer:
        return NO_ANSWER
    except Unusable as exc:
        log.info("Проверка %s не дала ответа: %s", url, exc)
        return None

    if resp.status != 200:
        return text_for_status(resp.status)
    if not is_html(resp):
        return NOT_A_PAGE
    if _robots_forbid(url):
        return ROBOTS
    if site_checks.looks_empty(resp.body):
        return NO_TEXT
    return None
