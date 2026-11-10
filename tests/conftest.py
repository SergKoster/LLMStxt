import socket

import pytest
from fastapi.testclient import TestClient

from app import config, tasks
from app.main import app

PUBLIC_IP = "93.184.216.34"


def fake_dns(mapping):
    """Подмена DNS: хост → IP. Неизвестный хост — как несуществующий сайт."""

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]

    return getaddrinfo


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Каждый тест — своя папка с файлами, пустой список задач и поддельный DNS."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tasks, "_tasks", {})
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_dns({"example.com": PUBLIC_IP, "localhost": "127.0.0.1", "intranet.test": "192.168.1.10"}),
    )


@pytest.fixture
def client():
    return TestClient(app)
