import pytest

from app import site_checks
from app.site_checks import Page, parse_page

GOOD_HEAD = (
    "<title>Главная</title>"
    '<meta name="description" content="Описание">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
)
TEXT = "Обычный текст страницы. " * 20


def html(head=GOOD_HEAD, body=TEXT):
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>".encode()


def page(url="https://example.com/", status=200, head=GOOD_HEAD, body=TEXT):
    return parse_page(url, status, html(head, body))


# --- разбор страницы ---


def test_parse_reads_title_description_viewport_and_links():
    result = page(body='<a href="/a">A</a><a href="https://other.com/">B</a>' + TEXT)
    assert result.title == "Главная"
    assert result.description == "Описание"
    assert result.has_viewport
    assert result.links == ["/a", "https://other.com/"]


def test_text_without_closing_head_still_counts():
    body = f"<html><head><title>T</title><body>{TEXT}</body></html>".encode()
    assert not site_checks.looks_empty(body)


def test_internal_links_keep_only_same_site_pages():
    main = page(
        body=(
            '<a href="/a">1</a><a href="/a#top">2</a><a href="https://www.example.com/b">3</a>'
            '<a href="https://other.com/">4</a><a href="/doc.pdf">5</a><a href="mailto:x@example.com">6</a>'
            '<a href="/">7</a><a href="/c">8</a>'
        )
    )
    assert site_checks.internal_links(main, limit=2) == ["https://example.com/a", "https://www.example.com/b"]


# --- формат llms.txt ---

STANDARD = b"""# Example

> Short description.

Some text.

## Docs

- [Start](https://example.com/start): how to begin
- [API](https://example.com/api)

## Optional

- [Blog](https://example.com/blog)
"""


@pytest.mark.parametrize(
    "body",
    [
        STANDARD,
        "﻿# Сайт\n\n## Раздел\n\n- [Страница](https://example.com/a)\n".encode(),
        b"# Only title\n",
        # Смягчённое правило (решение владельца 2026-11-12): так пишут хорошие ручные файлы
        b"# T\n## S\n### Deep\n- [A](https://e.com/a)\n",  # подзаголовок глубже ##
        b"# T\n## S\n- just text\n- The [API](https://e.com/api) for payments\n",  # пункты без ссылки в начале
        b"# T\n## S\n- [A](https://e.com/a)\n- [B](https://e.com/a)\n",  # повтор ссылки
        b"# T\n```\n# comment in code\n```\n",  # # внутри блока кода — не заголовок
        b"# T\n## S\n- [A](https://e.com/a) - description\n",  # описание через « - », а не «:»
    ],
)
def test_standard_llms_txt(body):
    assert site_checks.llms_txt_is_standard(body)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"Some text\n# Title\n",  # заголовок не первый
        b"# One\n# Two\n",  # два заголовка первого уровня
        b"# T\n## S\n- [A](/relative)\n",  # ссылка не абсолютная
        b"# T\n## S\n- [A](https://e.com/a\n",  # ссылка записана с ошибкой
        "# Т\n".encode("cp1251"),  # не UTF-8
    ],
)
def test_not_standard_llms_txt(body):
    assert not site_checks.llms_txt_is_standard(body)


# --- советы ---


def test_good_site_has_no_advice():
    main = page()
    assert site_checks.check_closed_in_robots("User-agent: *\nDisallow: /admin\n") is None
    assert site_checks.check_noindex(main, {}) is None
    assert site_checks.check_https("ok") is None
    assert site_checks.check_broken_pages([page("https://example.com/a")]) is None
    assert site_checks.check_empty_without_js(html()) is None
    assert site_checks.check_viewport(main) is None
    assert site_checks.check_titles([main, page("https://example.com/a", head="<title>Другая</title>"
                                                '<meta name="description" content="x">')]) is None
    assert site_checks.check_not_found(404) is None


@pytest.mark.parametrize(
    ("head", "headers"),
    [
        (GOOD_HEAD + '<meta name="robots" content="noindex, follow">', {}),
        (GOOD_HEAD + '<meta name="yandex" content="none">', {}),
        (GOOD_HEAD, {"x-robots-tag": "googlebot: noindex"}),
    ],
)
def test_noindex_is_possible_advice(head, headers):
    advice = site_checks.check_noindex(page(head=head), headers)
    assert advice and advice.title == "Главная страница скрыта от поиска"
    assert advice.group == site_checks.POSSIBLE


@pytest.mark.parametrize("robots", ["User-agent: *\nDisallow: /\n", "User-agent: Yandex\nDisallow: /\n"])
def test_closed_in_robots_is_important_advice(robots):
    advice = site_checks.check_closed_in_robots(robots)
    assert advice and advice.title == "Сайт закрыт от поисковиков"
    assert advice.group == site_checks.IMPORTANT


def test_each_check_has_its_group():
    important = [
        site_checks.check_https("none"),
        site_checks.check_https("bad_certificate"),
        site_checks.check_broken_pages([page("https://example.com/a", status=404)]),
    ]
    possible = [
        site_checks.check_empty_without_js(b"<html><body><div id='root'></div><script></script></body></html>"),
        site_checks.check_titles([page(head="")]),
        site_checks.check_not_found(200),
    ]
    assert all(advice and advice.group == site_checks.IMPORTANT for advice in important)
    assert all(advice and advice.group == site_checks.POSSIBLE for advice in possible)


@pytest.mark.parametrize(
    "robots",
    [
        # Как у lenta.ru: «Disallow: /?» закрывает только адреса с параметрами, не весь сайт
        "User-agent: Yandex\nDisallow: /search\nDisallow: /?\nDisallow: */amp$\n\nUser-agent: *\nDisallow: /?\n",
        # Как у gosuslugi.ru: главная разрешена отдельным правилом «Allow: /$»
        "User-agent: *\nAllow: /$\nDisallow: /\n",
        # Пустой Disallow ничего не запрещает
        "User-agent: *\nDisallow:\n",
        # Запрет только для другого робота
        "User-agent: BadBot\nDisallow: /\n",
    ],
)
def test_robots_that_do_not_close_the_site(robots):
    assert site_checks.check_closed_in_robots(robots) is None


def test_robots_longest_rule_wins():
    groups = site_checks.parse_robots("User-agent: *\nDisallow: /\nAllow: /public\n")
    assert site_checks.robots_allows(groups, ("*",), "/public/page")
    assert not site_checks.robots_allows(groups, ("*",), "/private")
    # Группа робота с несколькими User-agent подряд
    groups = site_checks.parse_robots("User-agent: Googlebot\nUser-agent: Yandex\nDisallow: /\n")
    assert not site_checks.robots_allows(groups, ("yandexbot", "yandex"), "/")
    assert site_checks.robots_allows(groups, ("*",), "/")


def test_https_problems():
    assert site_checks.check_https("none")
    assert site_checks.check_https("bad_certificate")


def test_broken_pages_listed_but_403_is_not_broken():
    advice = site_checks.check_broken_pages(
        [Page("https://example.com/a", 404), Page("https://example.com/b", 503), Page("https://example.com/c", 403)]
    )
    assert advice
    assert "https://example.com/a" in advice.details and "https://example.com/b" in advice.details
    assert "https://example.com/c" not in advice.details


def test_empty_without_js():
    assert site_checks.check_empty_without_js(html(body='<div id="root"></div><script src="/app.js"></script>'))
    assert site_checks.check_empty_without_js(b"   ")
    # Короткая страница без скриптов — не «пустая без JS»
    assert site_checks.check_empty_without_js(html(body="Короткий текст")) is None


def test_no_viewport():
    advice = site_checks.check_viewport(page(head="<title>T</title>"))
    assert advice and advice.code == site_checks.VIEWPORT_TAG


def test_titles_missing_or_same():
    no_meta = page(head="<title></title>")
    assert site_checks.check_titles([no_meta])
    same = [page("https://example.com/"), page("https://example.com/a")]
    advice = site_checks.check_titles(same)
    assert advice and any("Одинаковый заголовок" in line for line in advice.details)
    # Страницы с ошибкой не считаем
    assert site_checks.check_titles([page(), Page("https://example.com/x", 404)]) is None


def test_missing_page_answers_200():
    assert site_checks.check_not_found(200)
    assert site_checks.check_not_found(None) is None


def test_short_page_with_script_is_not_empty():
    # Как example.com: немного текста и скрипт аналитики — это не JS-приложение
    assert site_checks.check_empty_without_js(html(body="Короткий текст<script src='/s.js'></script>")) is None


def test_one_inner_page_without_description_is_not_worth_advice():
    pages = [page("https://example.com/")] + [page(f"https://example.com/{i}", head=f"<title>{i}</title>"
                                                   '<meta name="description" content="x">') for i in range(4)]
    pages.append(page("https://example.com/service", head="<title>Служебная</title>"))
    assert site_checks.check_titles(pages) is None


def test_robots_sitemaps_are_read_outside_groups():
    text = (
        "User-agent: *\nDisallow: /admin\n"
        "Sitemap: https://example.com/sitemap.xml  # основная\n"
        "sitemap: https://example.com/news.xml\n"
        "Sitemap: /relative.xml\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    assert site_checks.robots_sitemaps(text) == ["https://example.com/sitemap.xml", "https://example.com/news.xml"]


def test_looks_like_sitemap():
    assert site_checks.looks_like_sitemap("https://e.com/sitemap.xml", b'<?xml version="1.0"?><urlset>')
    assert site_checks.looks_like_sitemap("https://e.com/s.xml", b"<?xml version='1.0'?>\n<sitemapindex>")
    assert site_checks.looks_like_sitemap("https://e.com/s.xml.gz", b"\x1f\x8b\x08rest")
    assert not site_checks.looks_like_sitemap("https://e.com/sitemap.xml", b"just text")
