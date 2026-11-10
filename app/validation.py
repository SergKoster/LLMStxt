"""Проверка URL, который прислал пользователь."""

import ipaddress
import socket
from urllib.parse import urlparse

from app import config


class UrlError(ValueError):
    """URL не подходит. Текст ошибки показывается пользователю."""


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def validate_url(raw: str) -> str:
    """Возвращает очищенный URL или бросает UrlError с понятным текстом."""
    url = (raw or "").strip()
    if not url:
        raise UrlError("Введите адрес сайта.")
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

    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UrlError("Сайт не найден. Проверьте, нет ли опечатки в адресе.") from None

    # Не даём ходить через сервис во внутреннюю сеть сервера.
    if not infos or not all(_is_public_ip(info[4][0]) for info in infos):
        raise UrlError("Этот адрес недоступен для генерации.")

    return url
