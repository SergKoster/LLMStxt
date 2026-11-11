"""Проверка URL, который прислал пользователь."""

import ipaddress
import re
import socket
from urllib.parse import urlparse

from app import config

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class UrlError(ValueError):
    """URL не подходит. Текст ошибки показывается пользователю."""


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def check_public_host(host: str, port: int | None) -> None:
    """Хост существует и смотрит в интернет, а не во внутреннюю сеть сервера. Иначе UrlError."""
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UrlError("Сайт не найден. Проверьте, нет ли опечатки в адресе.") from None
    if not infos or not all(_is_public_ip(info[4][0]) for info in infos):
        raise UrlError("Этот адрес недоступен для генерации.")


def scheme_given(raw: str) -> bool:
    """Пользователь сам написал схему (`https://`, `http://` или любую другую)."""
    return bool(_SCHEME.match((raw or "").strip()))


def validate_url(raw: str) -> str:
    """Возвращает очищенный URL или бросает UrlError с понятным текстом.

    Адрес без схемы (`priisk.pro`) получает `https://`; какую схему брать на самом деле,
    решает generator перед обходом.
    """
    url = (raw or "").strip()
    if not url:
        raise UrlError("Введите адрес сайта.")
    if not scheme_given(url):
        url = f"https://{url}"
    if len(url) > config.MAX_URL_LENGTH:
        raise UrlError("Адрес слишком длинный.")
    if not url.lower().startswith(("http://", "https://")):
        raise UrlError("Адрес должен начинаться с http:// или https://")

    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise UrlError("Адрес записан с ошибкой.") from None
    if not host or any(ch.isspace() for ch in url):
        raise UrlError("Адрес записан с ошибкой.")

    # Не даём ходить через сервис во внутреннюю сеть сервера.
    check_public_host(host, port)
    return url
