import subprocess

import pytest

from app import config, generator
from app.generator import GenerationError, build_command, generate, pick_max_pages


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


def fake_run(returncode=0, stderr="", content="# Site\n"):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 180
        if content is not None:
            with open(command[command.index("--output") + 1], "w", encoding="utf-8") as file:
                file.write(content)
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    return run


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(generator, "read_crawl_delay", lambda url: 0.0)


def test_generate_writes_final_file(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run())
    output = tmp_path / "task.txt"
    generate("https://example.com", output)
    assert output.read_text(encoding="utf-8") == "# Site\n"
    assert not (tmp_path / "task.txt.part").exists()


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


def test_generate_timeout(tmp_path, monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GenerationError, match="180 секунд"):
        generate("https://example.com", tmp_path / "task.txt")


def test_generate_no_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(1, "ERROR: No pages found. Check the URL and try again.", None))
    with pytest.raises(GenerationError, match="Не удалось прочитать страницы"):
        generate("https://example.com", tmp_path / "task.txt")
    assert not (tmp_path / "task.txt").exists()


def test_generate_empty_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(0, "", ""))
    with pytest.raises(GenerationError):
        generate("https://example.com", tmp_path / "task.txt")
