import pytest

from app import probe
from app.probe import NoAnswer, Response, Unusable

PAGE = Response(200, "text/html; charset=utf-8", b"<html><body>Text</body></html>")


def fake_fetch(answers):
    """Подмена сети: url → Response или исключение. Остальные адреса — нет ответа."""

    def fetch(url, user_agent=probe.USER_AGENT):
        answer = answers.get(url, NoAnswer("timeout"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (401, probe.AUTH),
        (403, probe.BLOCKED),
        (429, probe.BLOCKED),
        (404, probe.NOT_FOUND),
        (502, probe.SERVER_ERROR),
    ],
)
def test_http_errors_get_their_text(monkeypatch, status, text):
    monkeypatch.setattr(probe, "fetch", fake_fetch({"https://example.com": Response(status, "text/html", b"")}))
    assert probe.diagnose("https://example.com") == text


def test_silent_site(monkeypatch):
    monkeypatch.setattr(probe, "fetch", fake_fetch({}))
    assert probe.diagnose("https://example.com") == probe.NO_ANSWER


def test_unusable_answer_gives_no_reason(monkeypatch):
    monkeypatch.setattr(probe, "fetch", fake_fetch({"https://example.com": Unusable("tls")}))
    assert probe.diagnose("https://example.com") is None


def test_file_instead_of_page(monkeypatch):
    pdf = Response(200, "application/pdf", b"%PDF-1.7")
    monkeypatch.setattr(probe, "fetch", fake_fetch({"https://example.com/a.pdf": pdf}))
    assert probe.diagnose("https://example.com/a.pdf") == probe.NOT_A_PAGE


def test_empty_page(monkeypatch):
    monkeypatch.setattr(probe, "fetch", fake_fetch({"https://example.com": Response(200, "text/html", b"  ")}))
    assert probe.diagnose("https://example.com") == probe.NO_TEXT


def test_robots_forbids_llms_generator(monkeypatch):
    robots = Response(200, "text/plain", b"User-agent: llms-generator\nDisallow: /\n")
    answers = {"https://example.com": PAGE, "https://example.com/robots.txt": robots}
    monkeypatch.setattr(probe, "fetch", fake_fetch(answers))
    assert probe.diagnose("https://example.com") == probe.ROBOTS


def test_robots_with_only_star_rules_is_ignored_like_llms_gen(monkeypatch):
    robots = Response(200, "text/plain", b"User-agent: *\nDisallow: /\n")
    answers = {"https://example.com": PAGE, "https://example.com/robots.txt": robots}
    monkeypatch.setattr(probe, "fetch", fake_fetch(answers))
    assert probe.diagnose("https://example.com") is None


def test_pick_scheme_prefers_https_even_with_error_status(monkeypatch):
    answers = {"https://example.com/a": Response(403, "text/html", b""), "http://example.com/a": PAGE}
    monkeypatch.setattr(probe, "fetch", fake_fetch(answers))
    assert probe.pick_scheme("https://example.com/a") == "https://example.com/a"


def test_pick_scheme_falls_back_to_http(monkeypatch):
    monkeypatch.setattr(probe, "fetch", fake_fetch({"http://example.com": PAGE}))
    assert probe.pick_scheme("https://example.com") == "http://example.com"


def test_pick_scheme_gives_up(monkeypatch):
    monkeypatch.setattr(probe, "fetch", fake_fetch({}))
    assert probe.pick_scheme("https://example.com") is None


def test_redirect_into_internal_network_is_refused():
    handler = probe._SafeRedirect()

    class Req:
        full_url = "https://example.com/"

    with pytest.raises(Unusable):
        handler.redirect_request(Req(), None, 302, "Found", {}, "http://localhost/admin")
    with pytest.raises(Unusable):
        handler.redirect_request(Req(), None, 302, "Found", {}, "file:///etc/passwd")
