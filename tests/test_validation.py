import pytest

from app.validation import UrlError, scheme_given, validate_url


def test_good_url_is_returned_trimmed():
    assert validate_url("  https://example.com/docs  ") == "https://example.com/docs"


@pytest.mark.parametrize("url", ["", "   ", "ftp://example.com", "javascript:alert(1)"])
def test_url_must_start_with_http(url):
    with pytest.raises(UrlError):
        validate_url(url)


def test_url_without_scheme_gets_https():
    assert validate_url(" example.com/docs ") == "https://example.com/docs"
    assert not scheme_given("example.com")
    assert scheme_given("http://example.com")


def test_internal_address_without_scheme_is_blocked():
    with pytest.raises(UrlError, match="недоступен"):
        validate_url("localhost:8000")


@pytest.mark.parametrize("url", ["http://", "https:///path", "https://exa mple.com", "https://example.com:99999"])
def test_broken_url(url):
    with pytest.raises(UrlError):
        validate_url(url)


def test_unknown_site_gives_clear_message():
    with pytest.raises(UrlError, match="Сайт не найден"):
        validate_url("https://no-such-site.example")


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://intranet.test/admin"])
def test_internal_addresses_are_blocked(url):
    with pytest.raises(UrlError, match="недоступен"):
        validate_url(url)


def test_too_long_url():
    with pytest.raises(UrlError, match="длинный"):
        validate_url("https://example.com/" + "a" * 3000)
