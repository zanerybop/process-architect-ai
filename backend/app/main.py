"""Local demo HTTP API; generated Python always uses the configured worker."""

import asyncio
import hmac
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from backend.app.auth import COOKIE_NAME, SESSION_SECONDS, AuthError, AuthStore
from backend.app.config import Settings
from backend.app.domain import Issue, PipelineError
from backend.app.llm import LiveProvider, MockProvider
from backend.app.revision_review import review_revision
from backend.app.runner import ContainerRunner
from backend.app.service import GenerationService
from backend.app.storage import Storage
from backend.app.xml_validation import MAX_XML_BYTES, SCHEMA_DIR, SCHEMA_FILES, validate_xml

MAX_REQUEST_BYTES = 2_100_000


def error_response(status_code: int, code: str, message: str, issues: list[dict] | None = None):
    return JSONResponse(
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        content={
            "status": "error",
            "detail": message,
            "issues": issues or [Issue(code=code, message=message).model_dump()],
        },
    )


class RequestSizeLimit:
    """Count streamed body bytes even when Content-Length is omitted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for key, value in scope.get("headers", []):
            if key.lower() != b"content-length":
                continue
            try:
                length = int(value)
            except ValueError:
                return await error_response(400, "INVALID_REQUEST", "Некорректный размер запроса.")(
                    scope,
                    receive,
                    send,
                )
            if length < 0 or length > MAX_REQUEST_BYTES:
                return await error_response(413, "INPUT_LIMIT", "Превышен размер запроса.")(
                    scope,
                    receive,
                    send,
                )
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > MAX_REQUEST_BYTES:
                    raise HTTPException(status_code=413, detail="Превышен размер запроса.")
            return message

        return await self.app(scope, limited_receive, send)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(InputModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: SecretStr = Field(min_length=10, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr):
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError as error:
            raise ValueError("Недопустимые символы Unicode.") from error
        return value


class ClarificationAnswer(InputModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)
    source_quote: str = Field(default="", max_length=4000)

    @field_validator("question", "answer")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Нужен непустой ответ.")
        value.encode("utf-8")
        return value


class GenerateRequest(InputModel):
    description: str = Field(min_length=1, max_length=20_000)
    request_id: str | None = Field(default=None, max_length=128)
    clarification_answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def bounded_context(self):
        size = len(self.description) + sum(
            len(item.question) + len(item.answer) + 20 for item in self.clarification_answers
        )
        if size + (70 if self.clarification_answers else 0) > 20_000:
            raise ValueError("Описание вместе с ответами превышает 20 000 символов.")
        if any(
            item.source_quote and item.source_quote not in self.description
            for item in self.clarification_answers
        ):
            raise ValueError("Цитата вопроса должна присутствовать в исходном описании.")
        return self

    @field_validator("description", "request_id")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.strip():
                raise ValueError("Нужна непустая строка.")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Недопустимые символы Unicode.") from exc
        return value


class XmlRequest(InputModel):
    xml: str = Field(min_length=1, max_length=MAX_XML_BYTES)

    @field_validator("xml")
    @classmethod
    def bounded_utf8(cls, value: str) -> str:
        try:
            if len(value.encode("utf-8")) > MAX_XML_BYTES:
                raise ValueError("XML превышает допустимый размер.")
        except UnicodeError as exc:
            raise ValueError("Недопустимые символы Unicode.") from exc
        return value


class RevisionRequest(XmlRequest):
    base_revision_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ReviewRequest(XmlRequest):
    generation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def create_app(settings=None, provider=None, runner=None, storage=None) -> FastAPI:
    progress_states = {}
    """Explicit injection is for tests; production has no local execution fallback."""
    settings = settings or Settings()
    storage = storage or Storage(settings.data_dir)
    auth = AuthStore(settings.data_dir)
    runner = runner or ContainerRunner(settings.worker_url)
    if provider is None:
        provider = (
            MockProvider()
            if settings.app_mode == "mock"
            else LiveProvider(
                base_url=settings.llm_api_base_url,
                model=settings.llm_model,
                api_key=settings.llm_api_key.get_secret_value(),
                timeout=settings.llm_timeout_seconds,
                max_tokens=settings.llm_max_tokens,
                token_parameter=settings.llm_token_parameter,
                response_format=settings.llm_response_format,
                reasoning_effort=settings.llm_reasoning_effort,
                profile=settings.llm_provider_profile,
                auth_scheme=settings.llm_auth_scheme,
                project=settings.llm_project,
            )
        )
    service = GenerationService(settings, provider, runner, storage)
    app = FastAPI(title="Process Architect AI", docs_url="/api/docs", redoc_url=None)
    app.add_middleware(RequestSizeLimit)
    app.state.settings, app.state.service = settings, service
    app.state.runner, app.state.storage = runner, storage
    app.state.auth = auth
    capacity = asyncio.Semaphore(2)
    recent: dict[str, deque[float]] = defaultdict(deque)

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        public = {
            "/api/health",
            "/api/examples",
            "/api/auth/session",
            "/api/auth/login",
        }
        path = request.url.path.rstrip("/")
        if path == "/api/auth/register":
            return error_response(404, "NOT_FOUND", "Запрошенный адрес не найден.")
        if path.startswith("/api/"):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                try:
                    origin_url = urlsplit(origin) if origin else None
                    valid_origin = not origin_url or (
                        origin_url.scheme in {"http", "https"}
                        and origin_url.netloc.lower() == request.headers.get("host", "").lower()
                        and not origin_url.username
                        and not origin_url.password
                    )
                except ValueError:
                    valid_origin = False
                if request.headers.get("sec-fetch-site") == "cross-site" or not valid_origin:
                    return error_response(403, "CSRF_INVALID", "Запрос с другого сайта запрещён.")
            if path == "/api/auth/login":
                if request.method == "POST" and (
                    request.headers.get("x-requested-with") != "ProcessArchitect"
                    or request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json"
                ):
                    return error_response(403, "CSRF_INVALID", "Обновите страницу и повторите вход.")
            if path not in public:
                session = await run_in_threadpool(auth.session, request.cookies.get(COOKIE_NAME))
                if session is None:
                    return error_response(401, "AUTH_REQUIRED", "Войдите в аккаунт, чтобы продолжить.")
                request.state.session = session
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    csrf = request.headers.get("x-csrf-token", "")
                    if not hmac.compare_digest(csrf.encode("utf-8"), session["csrf_token"].encode("ascii")):
                        return error_response(403, "CSRF_INVALID", "Обновите страницу и повторите действие.")
        response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def user_storage(request: Request):
        return storage.for_user(request.state.session["user"]["id"])

    @app.exception_handler(AuthError)
    async def auth_error(_request, exc):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # Default Pydantic errors echo raw input, including descriptions and XML.
        return error_response(422, "INVALID_REQUEST", "Проверьте формат и допустимый размер полей запроса.")

    @app.exception_handler(PipelineError)
    async def pipeline_error(_request, exc):
        status = {
            "NOT_FOUND": 404,
            "REVISION_CONFLICT": 409,
            "REVISION_UNAVAILABLE": 409,
            "GENERATION_EXISTS": 409,
            "RUNNER_UNAVAILABLE": 503,
            "STORAGE_CORRUPT": 503,
            "STORAGE_UNAVAILABLE": 503,
            "STORAGE_BUSY": 503,
            "WORKER_BUSY": 429,
            "INPUT_LIMIT": 413,
        }.get(exc.issue.code, 422)
        return error_response(status, exc.issue.code, exc.issue.message, [exc.issue.model_dump()])

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        code = "INPUT_LIMIT" if exc.status_code == 413 else "HTTP_ERROR"
        message = "Превышен размер запроса." if exc.status_code == 413 else "Запрос не может быть выполнен."
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _exc):
        return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка. Сохранённая схема не изменена.")

    @app.get("/api/auth/session")
    async def session(request: Request):
        return await run_in_threadpool(auth.session, request.cookies.get(COOKIE_NAME)) or {
            "user": None,
            "csrf_token": None,
        }

    @app.post("/api/auth/login")
    async def login(payload: Credentials, request: Request):
        auth.rate_limit.check(request.client.host if request.client else "local", payload.username)
        token, session_data = await run_in_threadpool(
            auth.login,
            payload.username,
            payload.password.get_secret_value(),
        )
        response = JSONResponse(session_data)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request):
        await run_in_threadpool(auth.revoke, request.cookies.get(COOKIE_NAME))
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE_NAME,
            path="/",
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
        )
        return response

    @app.get("/api/health")
    async def health():
        worker_available = await runner.health()
        xsd_available = all((SCHEMA_DIR / filename).is_file() for filename in SCHEMA_FILES)
        llm_configured = bool(
            getattr(provider, "base_url", settings.llm_api_base_url)
            and settings.llm_model
            and settings.llm_api_key.get_secret_value()
        )
        return {
            "status": "ok",
            "mode": settings.app_mode,
            "dependencies": {
                "worker": worker_available,
                "xsd_files_available": xsd_available,
                "llm_configured": llm_configured,
            },
            "generation_available": bool(
                worker_available and xsd_available and (settings.app_mode == "mock" or llm_configured)
            ),
        }

    @app.get("/api/examples")
    async def examples():
        from backend.app.examples import load_examples

        return await run_in_threadpool(load_examples)

    @app.post("/api/generate")
    async def generate(payload: GenerateRequest, request: Request):
        now = time.monotonic()
        client = request.state.session["user"]["id"]
        for key in list(recent):
            while recent[key] and recent[key][0] <= now - 60:
                recent[key].popleft()
            if not recent[key]:
                del recent[key]
        if len(recent) >= 1024 and client not in recent:
            return error_response(429, "RATE_LIMIT", "Слишком много клиентов; повторите запрос позднее.")
        requests = recent[client]
        if len(requests) >= 12:
            return error_response(429, "RATE_LIMIT", "Допустимо не более 12 построений в минуту.")
        if capacity.locked():
            return error_response(
                429, "GENERATION_BUSY", "Два построения уже выполняются. Повторите позднее."
            )
        requests.append(now)
        for key in list(progress_states):
            if now - progress_states[key]["started"] > 600:
                del progress_states[key]
        progress_key = (client, payload.request_id) if payload.request_id else None

        def record_progress(value):
            if progress_key:
                progress_states[progress_key] = {**value, "started": now}

        if progress_key and len(progress_states) >= 1024:
            return error_response(429, "RATE_LIMIT", "Слишком много запросов; повторите позднее.")
        record_progress({"phase": "queued", "attempt": 0})
        async with capacity:
            scoped_service = GenerationService(
                settings, provider, runner, user_storage(request), record_progress
            )
            try:
                result = await scoped_service.generate(
                    payload.description,
                    clarification_answers=[item.model_dump() for item in payload.clarification_answers],
                )
            except Exception:
                record_progress({"phase": "error", "attempt": 0})
                raise
        return {**result, "request_id": payload.request_id} if payload.request_id is not None else result

    @app.get("/api/generation-progress/{request_id}")
    async def generation_progress(request_id: str, request: Request):
        value = progress_states.get((request.state.session["user"]["id"], request_id))
        if value is None or time.monotonic() - value["started"] > 600:
            return error_response(404, "NOT_FOUND", "Состояние запроса недоступно.")
        return {
            "phase": value["phase"],
            "attempt": value["attempt"],
            "elapsed_seconds": round(time.monotonic() - value["started"], 1),
        }

    @app.get("/api/generations")
    async def generations(request: Request):
        return {"items": await run_in_threadpool(user_storage(request).list_generations)}

    @app.get("/api/generations/{generation_id}")
    async def generation(generation_id: str, request: Request):
        return await run_in_threadpool(user_storage(request).get, generation_id)

    @app.post("/api/validate")
    async def validate(payload: XmlRequest):
        return await run_in_threadpool(validate_xml, payload.xml)

    @app.get("/api/bpmn/schema")
    async def bpmn_schema():
        from backend.app.bpmn_schema import catalog

        return await run_in_threadpool(lambda: catalog().export())

    @app.post("/api/bpmn/layout")
    async def bpmn_layout(payload: XmlRequest):
        from lxml import etree

        from backend.app.document_serialization import arrange_document

        def arrange():
            validation = validate_xml(payload.xml)
            if validation["checks"].get("xsd") != "passed" or validation["checks"].get("graph") != "passed":
                return error_response(422, "INVALID_DOCUMENT", "Сначала исправьте структуру документа.",
                                      validation["issues"])
            root = etree.fromstring(payload.xml.encode("utf-8"), etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
            ))
            root = arrange_document(root, preserve_existing=False)
            xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True).decode("utf-8")
            return {"xml": xml, **validate_xml(xml)}

        return await run_in_threadpool(arrange)

    @app.post("/api/review")
    async def review(payload: ReviewRequest, request: Request):
        original = (
            await run_in_threadpool(user_storage(request).get, payload.generation_id)
            if payload.generation_id
            else None
        )
        return await run_in_threadpool(review_revision, payload.xml, original)

    @app.put("/api/generations/{generation_id}/revisions")
    async def revision(generation_id: str, payload: RevisionRequest, request: Request):
        scoped_storage = user_storage(request)
        await run_in_threadpool(scoped_storage.directory, generation_id)
        original = await run_in_threadpool(scoped_storage.get, generation_id)
        validation = await run_in_threadpool(review_revision, payload.xml, original)
        if any(validation["checks"].get(gate) != "passed" for gate in ("xsd", "graph")) or (
            validation["checks"].get("layout") == "failed"
        ) or (
            validation["checks"].get("behavior") == "failed"
        ):
            return error_response(
                422,
                "INVALID_REVISION",
                "Текущая схема не прошла технические проверки.",
                validation["issues"],
            )
        head = await run_in_threadpool(
            scoped_storage.revise,
            generation_id,
            payload.base_revision_id,
            payload.xml,
            validation,
        )
        return {"id": generation_id, **head}

    @app.get("/api/generations/{generation_id}/artifacts/{kind}")
    async def artifact(generation_id: str, kind: str, request: Request):
        path = await run_in_threadpool(user_storage(request).artifact, generation_id, kind)
        media_types = {"source": "text/plain", "python": "text/plain", "xml": "application/xml"}
        return FileResponse(
            path,
            media_type=media_types.get(kind, "application/json"),
            filename=path.name,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    return app


app = create_app()
