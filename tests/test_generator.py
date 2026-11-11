import subprocess
from pathlib import Path

import pytest

from app import config, generator, probe
from app.generator import GenerationError, build_command, generate, pick_max_pages

SITE_BYTES = b"# Site\n"


def test_pick_max_pages_without_crawl_delay_hits_the_cap():
    assert pick_max_pages(0) <= config.MAX_PAGES
    assert pick_max_pages(0) > 20


def test_pick_max_pages_shrinks_for_slow_sites():
    # habr.com просит 10 секунд между запросами — в минуту влезает всего несколько страниц
    pages = pick_max_pages(10)
    assert config.MIN_PAGES <= pages <= 6


def test_pick_max_pages_never_below_minimum():
    assert pick_max_pages(600) == config.MIN_PAGES


def test_command_follows_the_task(tmp_path):
    command = build_command("https://example.com", tmp_path / "x.txt", 10)
    assert command[1] == "https://example.com"
    assert command[command.index("--depth") + 1] == "3"
    assert command[command.index("--output") + 1] == str(tmp_path / "x.txt")


def test_full_flag_is_added_only_on_request(tmp_path):
    assert "--full" not in build_command("https://example.com", tmp_path / "llms.txt", 10)
    assert "--full" in build_command("https://example.com", tmp_path / "llms.txt", 10, full=True)


def fake_run(returncode=0, stderr="", content=SITE_BYTES):
    """Подмена llms-gen: пишет файлы туда же, куда писала бы настоящая команда."""

    def run(command, **kwargs):
        assert kwargs["timeout"] == 180
        output = Path(command[command.index("--output") + 1])
        if content is not None:
            output.write_bytes(content)
            if "--full" in command:
                output.with_stem("llms-full").write_bytes(content + "\nВесь текст страницы.\n".encode())
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    return run


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(generator, "read_crawl_delay", lambda url: 0.0)
    monkeypatch.setattr(probe, "diagnose", lambda url: None)


def test_generate_no_pages_names_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(1, "ERROR: No pages found. Check the URL and try again.", None))
    monkeypatch.setattr(probe, "diagnose", lambda url: probe.BLOCKED)
    with pytest.raises(GenerationError, match="защищён от автоматических программ"):
        generate("https://example.com", tmp_path / "work")


def test_guessed_scheme_is_used_for_crawl(tmp_path, monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append(command[1])
        return fake_run()(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(probe, "pick_scheme", lambda url: "http://example.com")
    generate("https://example.com", tmp_path / "work", guess_scheme=True)
    assert seen == ["http://example.com"]


def test_site_opens_by_no_scheme(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "pick_scheme", lambda url: None)
    with pytest.raises(GenerationError, match="ни по https://, ни по http://"):
        generate("https://example.com", tmp_path / "work", guess_scheme=True)


def test_generate_writes_llms_txt(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run())
    produced = generate("https://example.com", tmp_path / "work")
    assert list(produced) == ["llms.txt"]
    assert produced["llms.txt"].read_bytes() == SITE_BYTES


def test_generate_full_gives_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run())
    produced = generate("https://example.com", tmp_path / "work", full=True)
    assert list(produced) == ["llms.txt", "llms-full.txt"]
    assert "Весь текст страницы." in produced["llms-full.txt"].read_text(encoding="utf-8")


def test_missing_full_file_is_an_error(tmp_path, monkeypatch):
    def run(command, **kwargs):  # llms-gen написал только основной файл
        Path(command[command.index("--output") + 1]).write_bytes(SITE_BYTES)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GenerationError):
        generate("https://example.com", tmp_path / "work", full=True)


def test_broken_entries_are_glued_back():
    # так llms-gen пишет страницу, у которой в заголовке и описании есть перенос строки
    broken = (
        "# Сайт\n\n> Описание\n\nВводный текст.\n\n## Раздел\n\n"
        "- [Иван aka ivan\n     - Разработчик / Сайт](https://example.com/u/ivan/): Иван.\n"
        "- [Хаб](https://example.com/hub/): Первая строка.\nВторая строка.\n"
        "- [Обычная](https://example.com/a/): всё хорошо\n"
    )
    fixed = generator.fix_broken_entries(broken)
    assert "- [Иван aka ivan - Разработчик / Сайт](https://example.com/u/ivan/): Иван.\n" in fixed
    assert "- [Хаб](https://example.com/hub/): Первая строка. Вторая строка.\n" in fixed
    assert fixed.startswith("# Сайт\n\n> Описание\n\nВводный текст.\n\n## Раздел\n\n")
    assert fixed.endswith("- [Обычная](https://example.com/a/): всё хорошо\n")


def test_full_text_after_entry_stays_separate():
    # в llms-full.txt после пункта идёт пустая строка и текст страницы — его приклеивать нельзя
    full = "# Сайт\n\n## Раздел\n\n- [Страница](https://example.com/a/): описание\n\nТекст страницы.\n"
    assert generator.fix_broken_entries(full) == full


def test_generate_timeout(tmp_path, monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GenerationError, match="180 секунд"):
        generate("https://example.com", tmp_path / "work")


def test_generate_no_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(1, "ERROR: No pages found. Check the URL and try again.", None))
    with pytest.raises(GenerationError, match="Не удалось прочитать страницы"):
        generate("https://example.com", tmp_path / "work")


def test_generate_empty_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(0, "", b""))
    with pytest.raises(GenerationError):
        generate("https://example.com", tmp_path / "work")
