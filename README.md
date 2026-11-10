# Генератор llms.txt

Веб-сервис: вводишь адрес сайта — через минуту скачиваешь готовый `llms.txt`.

`llms.txt` — это короткий Markdown-файл в корне сайта с названием, описанием и списком главных
страниц. Его читают ИИ-ассистенты и агенты, чтобы быстро понять, что это за сайт
(формат: [llmstxt.org](https://llmstxt.org/)).

## Запуск

Нужен Docker.

```bash
docker-compose up --build
```

Сервис откроется на http://localhost:8000.

## Как пользоваться

1. Открыть страницу, ввести адрес сайта (обязательно с `http://` или `https://`).
2. Нажать «Сгенерировать» и подождать — обычно около минуты.
3. Браузер скачает `llms.txt`. Его нужно положить в корень сайта, чтобы он открывался по адресу
   `https://ваш-сайт/llms.txt`.

## API

| Метод | Путь | Что делает |
|---|---|---|
| `POST` | `/generate` | Тело `{"url": "https://..."}`. Запускает генерацию, возвращает `{"task_id": "...", "status": "processing"}`. Плохой адрес — `422` с `{"status": "error", "error": "..."}` |
| `GET` | `/status/{task_id}` | Готово — отдаёт файл `llms.txt`. Ещё нет — `{"status": "processing"}`. Ошибка — `{"status": "error", "error": "..."}` |

```bash
curl -X POST http://localhost:8000/generate -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
```

## Ограничения

- Одна генерация — не дольше 180 секунд, глубина обхода — 3 перехода от главной страницы.
- В файл попадает до 50 страниц. Если сайт в `robots.txt` просит ходить медленно (`Crawl-delay`),
  страниц будет меньше — сервис эту просьбу соблюдает.
- Страницы, которые рисуются только через JavaScript, не читаются.
- Готовые файлы хранятся 1 час, потом удаляются.
- Адреса внутренней сети (`localhost`, `192.168.x.x` и т.п.) не принимаются.

Все числа лежат в `app/config.py`.

## Разработка без Docker

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt    # Linux/macOS: .venv/bin/pip
.venv/Scripts/uvicorn app.main:app --reload
.venv/Scripts/python -m pytest
```

## Устройство

- `app/main.py` — маршруты FastAPI;
- `app/validation.py` — проверка адреса;
- `app/generator.py` — запуск библиотеки [`llms-generator`](https://pypi.org/project/llms-generator/) (команда `llms-gen`);
- `app/tasks.py` — список задач в памяти и фоновая генерация;
- `app/cleanup.py` — удаление старых файлов;
- `app/static/` — страница на HTML и обычном JS.

Подробнее — в `docs/ARCHITECTURE.md`.
