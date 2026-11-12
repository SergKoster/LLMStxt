"""Проверки сайта и llms.txt по уже скачанным ответам. В сеть этот модуль не ходит.

Каждая проверка возвращает Advice (проблема найдена) или None. Тексты советов — черновик
агента, утверждает владелец (docs/TASKS.md, 3.3.6).
"""

import re
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

# Меньше стольких символов видимого текста при заготовке под JS-приложение — «страница пустая без JS».
EMPTY_TEXT_CHARS = 200

# id контейнеров, в которые JS-фреймворки (React, Vue, Next, Nuxt, Gatsby…) рисуют сайт
_JS_MOUNT_IDS = {"root", "app", "__next", "__nuxt", "___gatsby", "svelte"}

# Расширения ссылок, которые ведут на файлы, а не на страницы
_FILE_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip", ".rar", ".7z",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp3", ".mp4", ".avi", ".mov", ".xml",
    ".txt", ".json", ".css", ".js", ".exe", ".apk", ".dmg",
)

# Роботы, для которых проверяем запрет в robots.txt
_SEARCH_ROBOTS = ("*", "Googlebot", "YandexBot")


@dataclass
class Advice:
    """Найденная проблема: короткая строка и разворачиваемое «что сделать»."""

    title: str
    details: list[str]
    code: str = ""  # готовая строка для вставки, если есть


@dataclass
class Page:
    """Разобранная HTML-страница."""

    url: str
    status: int
    title: str | None = None  # None — тега нет
    description: str | None = None
    has_viewport: bool = False
    robots_meta: list[str] = field(default_factory=list)  # content у meta robots/googlebot/yandex
    links: list[str] = field(default_factory=list)  # href как есть
    text_chars: int = 0  # видимый текст без пробелов
    has_scripts: bool = False
    has_js_mount: bool = False  # есть контейнер под JS-приложение


class _Parser(HTMLParser):
    _HIDDEN = {"script", "style", "noscript", "template", "head", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page = Page(url="", status=200)
        self._hidden_depth = 0
        self._in_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = {name.lower(): (value or "") for name, value in attrs}
        if tag == "script":
            self.page.has_scripts = True
        if attrs.get("id", "").strip().lower() in _JS_MOUNT_IDS:
            self.page.has_js_mount = True
        if tag == "body":  # </head> можно не писать — с <body> текст точно видимый
            self._hidden_depth = 0
        if tag == "title" and self.page.title is None:
            self._in_title = True
        elif tag == "meta":
            name = attrs.get("name", "").strip().lower()
            content = attrs.get("content", "")
            if name == "description" and self.page.description is None:
                self.page.description = content.strip()
            elif name == "viewport":
                self.page.has_viewport = True
            elif name in ("robots", "googlebot", "yandex"):
                self.page.robots_meta.append(content.lower())
        elif tag == "a" and attrs.get("href"):
            self.page.links.append(attrs["href"].strip())
        if tag in self._HIDDEN:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.page.title = " ".join("".join(self._title).split())
        if tag in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self._title.append(data)
        elif not self._hidden_depth:
            self.page.text_chars += len("".join(data.split()))


def parse_page(url: str, status: int, body: bytes) -> Page:
    parser = _Parser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # сломанный HTML — берём, что успели разобрать
        pass
    if parser._in_title:  # тег title не закрыт
        parser.page.title = " ".join("".join(parser._title).split())
    page = parser.page
    page.url, page.status = url, status
    return page


def looks_empty(body: bytes) -> bool:
    """Текста на странице нет совсем, или почти нет, а сайт рисует JS-приложение.

    Общее правило для анализа (совет «пустая без скриптов») и для probe (ошибка NO_TEXT).
    """
    page = parse_page("", 200, body)
    return page.text_chars == 0 or (page.text_chars < EMPTY_TEXT_CHARS and page.has_js_mount)


def _same_site(host_a: str, host_b: str) -> bool:
    strip = lambda host: host.lower().removeprefix("www.")  # noqa: E731
    return strip(host_a) == strip(host_b)


def internal_links(page: Page, limit: int) -> list[str]:
    """Ссылки со страницы на этот же сайт: без якорей, файлов и повторов, не больше limit."""
    base = urlparse(page.url)
    seen = {urldefrag(page.url).url.rstrip("/")}
    result = []
    for href in page.links:
        url = urldefrag(urljoin(page.url, href)).url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if not _same_site(parsed.hostname, base.hostname or ""):
            continue
        if parsed.path.lower().endswith(_FILE_EXTENSIONS):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
        if len(result) >= limit:
            break
    return result


# --- Формат llms.txt (правила — docs/RESEARCH.md, раздел 2) ---

_LINK_ITEM = re.compile(r"^[-*] \[[^\]]+\]\((https?://[^\s)]+)\)(:.*)?$")


def llms_txt_is_standard(body: bytes) -> bool:
    """Файл сделан по стандарту llmstxt.org."""
    try:
        text = body.decode("utf-8").lstrip("﻿")
    except UnicodeDecodeError:
        return False
    lines = [line.rstrip() for line in text.splitlines()]
    meaningful = [line for line in lines if line.strip()]
    if not meaningful or not meaningful[0].startswith("# "):
        return False
    headings = [line for line in meaningful if line.startswith("#")]
    if sum(1 for line in headings if line.startswith("# ")) != 1:
        return False
    if any(not line.startswith(("# ", "## ")) for line in headings):
        return False  # заголовки глубже ## или без пробела

    links = []
    in_section = False
    for line in meaningful:
        if line.startswith("## "):
            in_section = True
        elif in_section and line.startswith(("- ", "* ")):
            match = _LINK_ITEM.match(line)
            if not match:
                return False
            links.append(match.group(1))
    return len(links) == len(set(links))


# --- Советы по сайту (проверки 1, 2, 5, 6, 7, 8 из docs/RESEARCH.md, раздел 4) ---


def _forbids_index(robots_content: str) -> bool:
    words = set(re.split(r"[\s,:]+", robots_content.lower()))  # и «googlebot: noindex»
    return bool(words & {"noindex", "none"})


def check_closed_from_search(main: Page, headers: dict[str, str], robots_txt: str | None) -> Advice | None:
    """1. Сайт закрыт от поисковиков: noindex на главной или Disallow: / в robots.txt."""
    details = []
    if any(_forbids_index(content) for content in main.robots_meta):
        details.append(
            'На главной странице стоит запрет на попадание в поиск: <meta name="robots" content="noindex">. '
            "Удалите этот тег. В WordPress: «Настройки» → «Чтение» → снимите галочку "
            "«Попросить поисковые системы не индексировать сайт»."
        )
    if _forbids_index(headers.get("x-robots-tag", "")):
        details.append(
            "Сервер отдаёт заголовок X-Robots-Tag: noindex — он запрещает показывать сайт в поиске. "
            "Уберите его в настройках хостинга или попросите разработчика."
        )
    if robots_txt is not None:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_txt.splitlines())
        root = urljoin(main.url, "/")
        blocked = [robot for robot in _SEARCH_ROBOTS if not parser.can_fetch(robot, root)]
        if blocked:
            details.append(
                "Файл robots.txt запрещает поисковикам обходить сайт (строка «Disallow: /»). "
                "Уберите её или оставьте запрет только для служебных разделов."
            )
    if not details:
        return None
    return Advice("Сайт закрыт от поисковиков", details)


def check_https(https_state: str) -> Advice | None:
    """2а. Нет HTTPS или сертификат с ошибкой. https_state: ok / none / bad_certificate."""
    if https_state == "none":
        return Advice(
            "Сайт не открывается по защищённому адресу https://",
            [
                "Браузеры помечают такие сайты как «Не защищено», и посетители уходят. "
                "Включите HTTPS в панели хостинга — у большинства хостингов бесплатный сертификат "
                "подключается одной кнопкой (Let's Encrypt)."
            ],
        )
    if https_state == "bad_certificate":
        return Advice(
            "Сертификат https:// не проходит проверку",
            [
                "Сертификат сайта устарел или выписан на другой адрес, поэтому браузер показывает "
                "предупреждение об опасности. Продлите или перевыпустите сертификат в панели хостинга."
            ],
        )
    return None


def check_broken_pages(pages: list[Page]) -> Advice | None:
    """2б. Ссылки с главной ведут на страницы с ошибкой (404/410/5xx)."""
    broken = [page.url for page in pages if page.status in (404, 410) or page.status >= 500]
    if not broken:
        return None
    return Advice(
        "Часть страниц открывается с ошибкой",
        ["Ссылки с главной ведут на страницы, которые не открываются. Исправьте ссылки или верните страницы:"]
        + broken,
    )


def check_empty_without_js(main_body: bytes) -> Advice | None:
    """5. Без скриптов на главной почти нет текста."""
    if not looks_empty(main_body):
        return None
    return Advice(
        "Без скриптов на главной странице почти нет текста",
        [
            "Текст сайта появляется только после работы скриптов в браузере. Поисковики видят его "
            "не всегда, а большинство ИИ-ассистентов не видят совсем. Попросите разработчика включить "
            "серверную или предварительную отрисовку страниц (SSR или pre-rendering)."
        ],
    )


VIEWPORT_TAG = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def check_viewport(main: Page) -> Advice | None:
    """6. Нет строки для телефонов."""
    if main.has_viewport:
        return None
    return Advice(
        "Сайт не настроен для телефонов",
        [
            "В коде страницы нет строки viewport, поэтому на телефоне сайт показывается мелко, "
            "как уменьшенная копия компьютерной версии. Добавьте эту строку внутрь <head>:"
        ],
        code=VIEWPORT_TAG,
    )


def _widespread(bad: list[str], pages: list[Page]) -> bool:
    """Проблема стоит совета: она на главной или хотя бы на половине страниц.

    Иначе у большого сайта всегда найдётся пара служебных страниц без описания — это мелочь.
    """
    return bool(bad) and (pages[0].url in bad or len(bad) * 2 >= len(pages))


def check_titles(pages: list[Page]) -> Advice | None:
    """7. Нет заголовка или описания, или заголовки одинаковые на разных страницах.

    pages[0] — главная; страницы-дубли (тот же текст по другому адресу) убирает вызывающий.
    """
    html_pages = [page for page in pages if page.status == 200]
    if not html_pages:
        return None
    no_title = [page.url for page in html_pages if not page.title]
    no_description = [page.url for page in html_pages if not page.description]
    by_title: dict[str, list[str]] = {}
    for page in html_pages:
        if page.title:
            by_title.setdefault(page.title, []).append(page.url)
    same = [url for urls in by_title.values() if len(urls) > 1 for url in urls]

    details = []
    if _widespread(no_title, html_pages):
        details.append(f"Нет заголовка (тег title) — страниц: {len(no_title)}. Это название сайта в поиске.")
    if _widespread(no_description, html_pages):
        details.append(
            f"Нет описания (meta description) — страниц: {len(no_description)}. "
            "Это подпись под ссылкой в поиске."
        )
    if _widespread(same, html_pages):
        details.append(f"Одинаковый заголовок у {len(same)} страниц — в поиске их не отличить.")
    if not details:
        return None
    details.append(
        "Задайте каждой странице свой заголовок и короткое описание. В конструкторах сайтов это "
        "обычно настройки SEO страницы."
    )
    return Advice("Нет заголовка или описания для поиска", details)


def check_not_found(status_of_missing_page: int | None) -> Advice | None:
    """8. Несуществующий адрес отвечает 200 вместо 404."""
    if status_of_missing_page != 200:
        return None
    return Advice(
        "Несуществующие страницы не отвечают ошибкой 404",
        [
            "Мы открыли адрес, которого на сайте нет, и сайт ответил как обычной страницей. "
            "Поисковики могут принять такие адреса за настоящие страницы. Настройте ответ 404 "
            "для несуществующих страниц в панели хостинга или попросите разработчика."
        ],
    )
