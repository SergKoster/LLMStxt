import pytest

from app import analysis, probe
from app.probe import BadCertificate, NoAnswer, Response

GOOD_HEAD = (
    "<title>{title}</title>"
    '<meta name="description" content="Описание">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
)
TEXT = "Обычный текст страницы. " * 20


def html_page(url, title="Главная", body=TEXT, status=200):
    content = f"<html><head>{GOOD_HEAD.format(title=title)}</head><body>{body}</body></html>".encode()
    return Response(status, "text/html; charset=utf-8", content, {}, url)


def text_file(url, body):
    return Response(200, "text/plain; charset=utf-8", body, {}, url)


STANDARD_LLMS = b"# Example\n\n## Docs\n\n- [A](https://example.com/a)\n"


def site(**extra):
    """Хороший сайт: главная со ссылкой на /a, 404 на всё неизвестное."""
    answers = {
        "https://example.com/": html_page("https://example.com/", body='<a href="/a">A</a>' + TEXT),
        "https://example.com/a": html_page("https://example.com/a", title="Страница A"),
        "https://example.com/robots.txt": text_file("https://example.com/robots.txt", b"User-agent: *\nAllow: /\n"),
    }
    answers.update(extra)
    return answers


@pytest.fixture
def network(monkeypatch):
    """Подмена сети: url → Response или исключение. Неизвестный адрес — 404."""
    answers = {}

    def fetch(url, user_agent=probe.USER_AGENT, timeout=None):
        answer = answers.get(url, Response(404, "text/html", b"", {}, url))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(probe, "fetch", fetch)
    return answers


def files_of(result):
    return {item["name"]: item for item in result["files"]}


def test_good_site_without_llms_files(network):
    network.update(site())
    result = analysis.analyze("https://example.com/")
    assert result["advice"] == []
    files = files_of(result)
    assert files["llms.txt"]["recommended"] and files["llms.txt"]["reason"] == analysis.NO_LLMS
    assert files["llms-full.txt"]["recommended"]
    assert files["llms.txt"]["about"]


def test_standard_llms_txt_is_not_recommended(network):
    network.update(site(**{"https://example.com/llms.txt": text_file("https://example.com/llms.txt", STANDARD_LLMS)}))
    files = files_of(analysis.analyze("https://example.com/"))
    assert not files["llms.txt"]["recommended"]
    assert files["llms.txt"]["reason"] == analysis.LLMS_STANDARD


def test_broken_llms_txt_is_recommended_with_its_reason(network):
    broken = text_file("https://example.com/llms.txt", b"just some text\n")
    network.update(site(**{"https://example.com/llms.txt": broken}))
    files = files_of(analysis.analyze("https://example.com/"))
    assert files["llms.txt"]["recommended"]
    assert files["llms.txt"]["reason"] == analysis.LLMS_NOT_STANDARD


def test_html_instead_of_file_means_no_file(network):
    # Частый случай: сайт на любой адрес отдаёт главную с кодом 200
    network.update(site(**{
        "https://example.com/llms.txt": html_page("https://example.com/llms.txt"),
        "https://example.com/llms-full.txt": html_page("https://example.com/llms-full.txt"),
    }))
    files = files_of(analysis.analyze("https://example.com/"))
    assert files["llms.txt"]["reason"] == analysis.NO_LLMS
    assert files["llms-full.txt"]["reason"] == analysis.NO_FULL


def test_existing_llms_full_is_not_recommended(network):
    network.update(site(**{"https://example.com/llms-full.txt": text_file("https://example.com/llms-full.txt", b"x")}))
    assert not files_of(analysis.analyze("https://example.com/"))["llms-full.txt"]["recommended"]


def test_problems_become_advice(network):
    network.update(site(**{
        "http://example.com/": Response(
            200, "text/html",
            b"<html><head><title>T</title></head><body><a href='/gone'>x</a>"
            b"<div id='root'></div><script src='/app.js'></script></body></html>",
            {"x-robots-tag": "noindex"}, "http://example.com/",
        ),
        "http://example.com/robots.txt": text_file("http://example.com/robots.txt", b""),
        "https://example.com/": NoAnswer("no https"),
    }))
    # Несуществующий адрес отвечает главной с кодом 200
    real_fetch = probe.fetch

    def fetch(url, user_agent=probe.USER_AGENT, timeout=None):
        if "/llms-check-" in url:
            return Response(200, "text/html", b"<html></html>", {}, url)
        return real_fetch(url, user_agent, timeout)

    probe.fetch = fetch
    titles = {item["title"] for item in analysis.analyze("http://example.com/")["advice"]}
    assert titles == {
        "Сайт закрыт от поисковиков",
        "Сайт не открывается по защищённому адресу https://",
        "Часть страниц открывается с ошибкой",
        "Без скриптов на главной странице почти нет текста",
        "Сайт не настроен для телефонов",
        "Нет заголовка или описания для поиска",
        "Несуществующие страницы не отвечают ошибкой 404",
    }


def test_bad_certificate_on_https_version(network):
    network.update({
        "http://example.com/": html_page("http://example.com/"),
        "https://example.com/": BadCertificate("expired"),
    })
    titles = [item["title"] for item in analysis.analyze("http://example.com/")["advice"]]
    assert titles == ["Сертификат https:// не проходит проверку"]


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (NoAnswer("timeout"), probe.NO_ANSWER),
        (BadCertificate("expired"), analysis.BAD_CERTIFICATE),
        (Response(403, "text/html", b"", {}, "https://example.com/"), probe.BLOCKED),
        (Response(200, "application/pdf", b"%PDF", {}, "https://example.com/"), probe.NOT_A_PAGE),
    ],
)
def test_main_page_problems_stop_analysis(network, answer, text):
    network["https://example.com/"] = answer
    with pytest.raises(analysis.AnalysisError, match=text[:20]):
        analysis.analyze("https://example.com/")


def test_scheme_guess_falls_back_to_http(network):
    network.update({"http://example.com/": html_page("http://example.com/"), "https://example.com/": NoAnswer("x")})
    assert analysis.analyze("https://example.com/", guess_scheme=True)["url"] == "http://example.com/"


def test_api_analyze(client, network):
    network.update(site())
    response = client.post("/analyze", json={"url": "https://example.com/"})
    assert response.status_code == 200
    assert response.json()["status"] == "done"


def test_api_analyze_errors(client, network):
    assert client.post("/analyze", json={"url": "http://localhost/"}).status_code == 422
    network["https://example.com/"] = NoAnswer("timeout")
    response = client.post("/analyze", json={"url": "https://example.com/"})
    assert response.status_code == 422
    assert response.json()["error"] == probe.NO_ANSWER


def test_api_analyze_busy(client, network, monkeypatch):
    network.update(site())
    monkeypatch.setattr(analysis, "_slots", analysis.threading.BoundedSemaphore(1))
    analysis._slots.acquire()
    assert client.post("/analyze", json={"url": "https://example.com/"}).status_code == 429


def test_same_page_under_two_addresses_is_not_a_duplicate_title(network):
    main = html_page("https://example.com/", body='<a href="/index.html">A</a>' + TEXT)
    network.update(site(**{
        "https://example.com/": main,
        "https://example.com/index.html": Response(200, main.content_type, main.body, {}, "https://example.com/index.html"),
    }))
    assert analysis.analyze("https://example.com/")["advice"] == []


def test_big_llms_txt_cut_mid_line_is_still_standard(network, monkeypatch):
    monkeypatch.setattr(probe, "MAX_BODY_BYTES", 40)
    body = b"# Example\n## Docs\n- [A](https://example.com/a)\n- [B](https://exam"[:40]
    network.update(site(**{"https://example.com/llms.txt": text_file("https://example.com/llms.txt", body)}))
    assert not files_of(analysis.analyze("https://example.com/"))["llms.txt"]["recommended"]
