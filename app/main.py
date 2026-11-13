"""FastAPI-приложение: страница, POST /analyze, POST /generate, GET /status/{task_id}."""

import asyncio
import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import analysis, config, tasks
from app.cleanup import cleanup_loop
from app.validation import UrlError, scheme_given, validate_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"
SITEMAP_PARTIAL_HEADER = "X-Sitemap-Partial"
_MEDIA_TYPES = {".zip": "application/zip", ".xml": "application/xml; charset=utf-8"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleaner = asyncio.create_task(cleanup_loop())
    yield
    cleaner.cancel()


app = FastAPI(title="Генератор llms.txt", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AnalyzeRequest(BaseModel):
    url: str


class GenerateRequest(BaseModel):
    url: str
    files: list[str] = [config.LLMS_FILE]  # что генерировать; по умолчанию — как в задании


def _error(status_code: int, text: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "error": text})


def _index_with_versions() -> str:
    """index.html, где к ссылкам на app.js и style.css дописан отпечаток их содержимого.

    Файл поменялся — поменялась ссылка, и браузер не возьмёт старую версию из кэша.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f"/static/{name}", f"/static/{name}?v={digest}")
    return html


INDEX_HTML = _index_with_versions()  # статика меняется только с перезапуском сервиса


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    # no-cache: саму страницу браузер каждый раз сверяет с сервером, иначе не увидит новые ссылки
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})


@app.post("/analyze")
def analyze_site(body: AnalyzeRequest):
    """Смотрит сайт до генерации: какие файлы рекомендовать и что на сайте поправить.

    Обычная (не async) функция: FastAPI выполнит её в отдельном потоке, запросы к сайту
    не держат остальной сервис.
    """
    try:
        url = validate_url(body.url)
        return analysis.analyze(url, guess_scheme=not scheme_given(body.url))
    except UrlError as exc:
        return _error(422, str(exc))
    except analysis.AnalysisError as exc:
        return _error(422, str(exc))
    except analysis.Busy:
        return _error(429, analysis.BUSY)


@app.post("/generate")
def start_generation(body: GenerateRequest, background: BackgroundTasks):
    try:
        url = validate_url(body.url)
    except UrlError as exc:
        return _error(422, str(exc))

    # Убираем повторы и приводим к одному порядку
    files = [name for name in config.AVAILABLE_FILES if name in body.files]
    if not files or set(body.files) - set(config.AVAILABLE_FILES):
        return _error(422, "Выберите, что сгенерировать.")

    try:
        task_id = tasks.create_task()
    except tasks.TooManyTasks:
        return _error(429, "Сервис сейчас занят. Попробуйте через минуту.")

    background.add_task(tasks.run_task, task_id, url, files, not scheme_given(body.url))
    return {"task_id": task_id, "status": tasks.PROCESSING}


@app.get("/status/{task_id}")
def get_status(task_id: str):
    try:
        task_id = str(uuid.UUID(task_id))
    except ValueError:
        return _error(404, "Задача не найдена.")

    task = tasks.get_task(task_id)
    if task and task.status == tasks.PROCESSING:
        return {"status": tasks.PROCESSING}
    if task and task.status == tasks.ERROR:
        return {"status": tasks.ERROR, "error": task.error}

    # Задача готова — либо сервис перезапускался, а файл на диске остался.
    result = tasks.find_result(task_id)
    if result:
        path, download_name = result
        media_type = _MEDIA_TYPES.get(path.suffix, "text/plain; charset=utf-8")
        headers = {}
        if task and task.sitemap_partial:
            # Карта сайта неполная — страница покажет пометку (сколько страниц вошло)
            headers[SITEMAP_PARTIAL_HEADER] = str(task.sitemap_partial)
        return FileResponse(path, media_type=media_type, filename=download_name, headers=headers)
    return _error(404, "Задача не найдена или файл уже удалён. Сгенерируйте заново.")
