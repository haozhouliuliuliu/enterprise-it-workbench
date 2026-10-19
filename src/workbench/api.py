"""受控 HTTP API：认证、任务隔离、健康检查与安全错误响应。"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .auth import AuthError, User
from .config import Settings
from .ingest import import_document
from .knowledge import MAX_FILE_BYTES, KnowledgeError
from .secure_logging import configure_logging
from .service import (
    InputTooLargeError,
    NotFoundError,
    OverloadedError,
    RateLimitError,
    ServiceError,
    TaskService,
    public_task,
)

logger = logging.getLogger("secure_agent.api")


class TaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20000)


class DecisionBody(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    params_hash: str | None = None
    reason: str = Field(default="", max_length=300)


def _secrets(settings: Settings) -> tuple[str, ...]:
    values = [item.key for item in settings.api_keys]
    for config in (settings.planner, settings.business, settings.policy, settings.auditor):
        if config is not None:
            values.append(config.api_key)
    return tuple(value for value in values if value)


def create_app(settings: Settings | None = None, *, service: TaskService | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    configure_logging(resolved.log_level, _secrets(resolved))
    app_service = service or TaskService(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        recovered = app_service.recover_interrupted()
        logger.info("service started", extra={"status": 200, "path": f"recovered={recovered}"})
        yield
        app_service.shutdown()
        logger.info("service stopped")

    app = FastAPI(title="Secure Agent Runtime", version="1.0.0", lifespan=lifespan)
    app.state.service = app_service
    app.state.key_store = None

    def get_service(request: Request) -> TaskService:
        return request.app.state.service

    def get_user(request: Request, authorization: str | None = Header(default=None)) -> User:
        from .auth import KeyStore

        store = request.app.state.key_store
        if store is None:
            store = KeyStore(request.app.state.service.settings.api_keys)
            request.app.state.key_store = store
        return store.authenticate(authorization)

    def require_admin(user: User = Depends(get_user)) -> User:
        if not user.is_admin:
            raise ServiceError(403, "FORBIDDEN")
        return user

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > resolved.max_input_chars * 4 + 4096:
            return JSONResponse(status_code=413, content={"error": "INPUT_TOO_LARGE"})
        response = await call_next(request)
        duration = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
            },
        )
        return response

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "TASK_NOT_FOUND"})

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"error": "RATE_LIMITED"})

    @app.exception_handler(OverloadedError)
    async def overloaded_handler(request: Request, exc: OverloadedError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "OVERLOADED"})

    @app.exception_handler(InputTooLargeError)
    async def too_large_handler(request: Request, exc: InputTooLargeError) -> JSONResponse:
        return JSONResponse(status_code=413, content={"error": "INPUT_TOO_LARGE"})

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(service: TaskService = Depends(get_service)) -> JSONResponse:
        if service.ready():
            return JSONResponse(status_code=200, content={"status": "ready"})
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    @app.get("/v1/me")
    def me(user: User = Depends(get_user)) -> JSONResponse:
        return JSONResponse(content={"user": user.user, "role": user.role, "department": user.department})

    @app.post("/v1/tasks", status_code=202)
    def submit(
        body: TaskRequest,
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        task_id = service.submit(user, body.request)
        return JSONResponse(
            status_code=202,
            content={"task_id": task_id, "status": "accepted", "owner": user.user},
        )

    @app.get("/v1/tasks")
    def list_tasks(
        limit: int = 20,
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        limit = max(1, min(limit, 100))
        rows = [public_task(task) for task in service.list_tasks(user, limit=limit)]
        return JSONResponse(content={"items": rows, "count": len(rows)})

    @app.get("/v1/tasks/{task_id}")
    def get_task(
        task_id: str,
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        task = service.get_task(user, task_id)
        proposal = service.get_proposal_for_task(task_id)
        return JSONResponse(content=public_task(task, proposal))

    @app.get("/v1/approvals")
    def pending_approvals(
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        rows = service.pending_approvals(user)
        return JSONResponse(content={"items": rows, "count": len(rows)})

    @app.post("/v1/approvals/{proposal_id}/decision")
    def decide(
        proposal_id: str,
        body: DecisionBody,
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        return JSONResponse(content=service.decide(user, proposal_id, body.decision, body.params_hash))

    @app.get("/v1/knowledge/documents")
    def knowledge_documents(
        user: User = Depends(require_admin),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        documents = service.system.tools.knowledge.list_documents()
        return JSONResponse(content={"items": documents, "count": len(documents)})

    @app.post("/v1/knowledge/documents", status_code=201)
    async def upload_document(
        file: UploadFile = File(...),
        title: str = Form(...),
        doc_id: str = Form(...),
        version: str = Form(...),
        status: str = Form("current"),
        doc_type: str = Form(""),
        source: str = Form("upload"),
        doc_group: str = Form(""),
        effective_from: str | None = Form(None),
        effective_to: str | None = Form(None),
        user: User = Depends(require_admin),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return JSONResponse(status_code=413, content={"error": "FILE_TOO_LARGE"})
        try:
            result = import_document(
                service.system.tools.knowledge,
                filename=file.filename or "upload",
                data=data,
                title=title,
                doc_id=doc_id,
                version=version,
                status=status,
                doc_type=doc_type,
                source=source,
                doc_group=doc_group,
                effective_from=effective_from,
                effective_to=effective_to,
            )
        except KnowledgeError as exc:
            status_code = 409 if exc.code in {"DUPLICATE_DOCUMENT", "DUPLICATE_VERSION"} else 400
            return JSONResponse(status_code=status_code, content={"error": exc.code})
        return JSONResponse(status_code=201, content=result)

    @app.delete("/v1/knowledge/documents/{doc_pk}", status_code=204)
    def delete_document(
        doc_pk: int,
        user: User = Depends(require_admin),
        service: TaskService = Depends(get_service),
    ) -> Response:
        if not service.system.tools.knowledge.delete_document(doc_pk):
            return JSONResponse(status_code=404, content={"error": "DOCUMENT_NOT_FOUND"})
        return Response(status_code=204)

    @app.post("/v1/knowledge/reindex")
    def reindex(
        user: User = Depends(require_admin),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        count = service.system.tools.knowledge.reindex()
        return JSONResponse(content={"reindexed_chunks": count})

    @app.get("/", response_class=HTMLResponse)
    def workbench_ui() -> HTMLResponse:
        from .ui import INDEX_HTML

        return HTMLResponse(INDEX_HTML)

    @app.get("/v1/admin/tasks")
    def admin_tasks(
        limit: int = 50,
        user: User = Depends(require_admin),
        service: TaskService = Depends(get_service),
    ) -> JSONResponse:
        limit = max(1, min(limit, 100))
        rows = [public_task(task) for task in service.repository.list_tasks(limit=limit)]
        return JSONResponse(content={"items": rows, "count": len(rows)})

    @app.delete("/v1/tasks/{task_id}", status_code=204)
    def delete_task(
        task_id: str,
        user: User = Depends(get_user),
        service: TaskService = Depends(get_service),
    ) -> Response:
        service.delete_task(user, task_id)
        return Response(status_code=204)

    return app
