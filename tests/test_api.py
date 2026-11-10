import uuid

from app import config, tasks
from app.generator import GenerationError


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Сгенерировать" in response.text


def test_generate_then_download(client, monkeypatch):
    def fake_generate(url, output):
        output.write_bytes(b"# Example\n")  # bytes: на Windows write_text превращает \n в \r\n

    monkeypatch.setattr(tasks, "generate", fake_generate)

    response = client.post("/generate", json={"url": "https://example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    uuid.UUID(body["task_id"])

    # TestClient выполняет фоновую задачу до возврата ответа, поэтому файл уже готов
    status = client.get(f"/status/{body['task_id']}")
    assert status.status_code == 200
    assert status.text == "# Example\n"
    assert status.headers["content-disposition"] == 'attachment; filename="llms.txt"'


def test_status_processing(client):
    task_id = tasks.create_task()
    assert client.get(f"/status/{task_id}").json() == {"status": "processing"}


def test_status_error_has_text(client, monkeypatch):
    def fake_generate(url, output):
        raise GenerationError("Сайт не отвечает.")

    monkeypatch.setattr(tasks, "generate", fake_generate)
    task_id = client.post("/generate", json={"url": "https://example.com"}).json()["task_id"]
    response = client.get(f"/status/{task_id}")
    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "Сайт не отвечает."}


def test_unexpected_crash_becomes_error_not_500(client, monkeypatch):
    def fake_generate(url, output):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "generate", fake_generate)
    task_id = client.post("/generate", json={"url": "https://example.com"}).json()["task_id"]
    body = client.get(f"/status/{task_id}").json()
    assert body["status"] == "error"
    assert "boom" not in body["error"]


def test_nonexistent_site_is_clear_error(client):
    response = client.post("/generate", json={"url": "https://no-such-site.example"})
    assert response.status_code == 422
    assert response.json()["status"] == "error"
    assert "Сайт не найден" in response.json()["error"]


def test_url_without_http_is_rejected(client):
    response = client.post("/generate", json={"url": "example.com"})
    assert response.status_code == 422
    assert "http" in response.json()["error"]


def test_unknown_task(client):
    assert client.get(f"/status/{uuid.uuid4()}").status_code == 404
    assert client.get("/status/not-a-uuid").status_code == 404
    assert client.get("/status/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_too_many_tasks(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_TASKS", 1)
    tasks.create_task()
    response = client.post("/generate", json={"url": "https://example.com"})
    assert response.status_code == 429
