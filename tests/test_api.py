import io
import uuid
import zipfile

from app import config, tasks
from app.generator import GenerationError

LLMS_BYTES = b"# Example\n"
FULL_BYTES = b"# Example\n\nFull text.\n"


def fake_generate(url, work_dir, full=False, guess_scheme=False):
    """Подмена генератора: кладёт файлы в рабочую папку, как настоящий."""
    work_dir.mkdir(parents=True)
    produced = {"llms.txt": work_dir / "llms.txt"}
    produced["llms.txt"].write_bytes(LLMS_BYTES)
    if full:
        produced["llms-full.txt"] = work_dir / "llms-full.txt"
        produced["llms-full.txt"].write_bytes(FULL_BYTES)
    return produced


def start(client, files):
    response = client.post("/generate", json={"url": "https://example.com", "files": files})
    assert response.status_code == 200
    # TestClient выполняет фоновую задачу до возврата ответа, поэтому результат уже готов
    return client.get(f"/status/{response.json()['task_id']}")


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Сгенерировать" in response.text


def test_generate_then_download(client, monkeypatch):
    monkeypatch.setattr(tasks, "generate", fake_generate)

    # без поля files — как в задании: только llms.txt
    response = client.post("/generate", json={"url": "https://example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    uuid.UUID(body["task_id"])

    status = client.get(f"/status/{body['task_id']}")
    assert status.status_code == 200
    assert status.content == LLMS_BYTES
    assert status.headers["content-disposition"] == 'attachment; filename="llms.txt"'
    assert not list(config.OUTPUT_DIR.glob("*.work"))  # рабочая папка убрана


def test_only_full_file_comes_without_archive(client, monkeypatch):
    monkeypatch.setattr(tasks, "generate", fake_generate)
    status = start(client, ["llms-full.txt"])
    assert status.headers["content-disposition"] == 'attachment; filename="llms-full.txt"'
    assert status.content == FULL_BYTES


def test_two_files_come_as_archive(client, monkeypatch):
    monkeypatch.setattr(tasks, "generate", fake_generate)
    status = start(client, ["llms-full.txt", "llms.txt"])
    assert status.headers["content-disposition"] == 'attachment; filename="llms.zip"'
    assert status.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(status.content)) as archive:
        assert archive.namelist() == ["llms.txt", "llms-full.txt"]
        assert archive.read("llms.txt") == LLMS_BYTES
        assert archive.read("llms-full.txt") == FULL_BYTES


def test_nothing_or_unknown_file_selected(client):
    for files in ([], ["robots.txt"], ["llms.txt", "../etc/passwd"]):
        response = client.post("/generate", json={"url": "https://example.com", "files": files})
        assert response.status_code == 422
        assert response.json()["status"] == "error"


def test_status_processing(client):
    task_id = tasks.create_task()
    assert client.get(f"/status/{task_id}").json() == {"status": "processing"}


def test_status_error_has_text(client, monkeypatch):
    def failing(url, work_dir, full=False, guess_scheme=False):
        raise GenerationError("Сайт не отвечает.")

    monkeypatch.setattr(tasks, "generate", failing)
    task_id = client.post("/generate", json={"url": "https://example.com"}).json()["task_id"]
    response = client.get(f"/status/{task_id}")
    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "Сайт не отвечает."}


def test_unexpected_crash_becomes_error_not_500(client, monkeypatch):
    def crashing(url, work_dir, full=False, guess_scheme=False):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "generate", crashing)
    task_id = client.post("/generate", json={"url": "https://example.com"}).json()["task_id"]
    body = client.get(f"/status/{task_id}").json()
    assert body["status"] == "error"
    assert "boom" not in body["error"]


def test_nonexistent_site_is_clear_error(client):
    response = client.post("/generate", json={"url": "https://no-such-site.example"})
    assert response.status_code == 422
    assert response.json()["status"] == "error"
    assert "Сайт не найден" in response.json()["error"]


def test_url_without_scheme_is_accepted_and_guessed(client, monkeypatch):
    calls = []

    def generate(url, work_dir, full=False, guess_scheme=False):
        calls.append((url, guess_scheme))
        return fake_generate(url, work_dir, full)

    monkeypatch.setattr(tasks, "generate", generate)
    assert client.post("/generate", json={"url": "example.com"}).status_code == 200
    assert client.post("/generate", json={"url": "http://example.com"}).status_code == 200
    assert calls == [("https://example.com", True), ("http://example.com", False)]


def test_unknown_task(client):
    assert client.get(f"/status/{uuid.uuid4()}").status_code == 404
    assert client.get("/status/not-a-uuid").status_code == 404
    assert client.get("/status/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_too_many_tasks(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_TASKS", 1)
    tasks.create_task()
    response = client.post("/generate", json={"url": "https://example.com"})
    assert response.status_code == 429
