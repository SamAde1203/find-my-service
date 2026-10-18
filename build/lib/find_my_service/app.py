"""HTTP API and an accessible web page.

Endpoints:
    GET  /              Ask a question (works without JavaScript)
    POST /              Form submission from that page
    POST /api/ask       JSON API: {"question": "..."}
    GET  /health        Liveness: the process is up
    GET  /ready         Readiness: the content index is loaded
    GET  /metrics       Counters and today's AI usage and cost
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from find_my_service.assistant import Assistant
from find_my_service.config import PACKAGE_DIR, Settings, get_settings
from find_my_service.models import Answer, AskRequest
from find_my_service.ops import Metrics, RateLimiter, configure_logging, request_id
from find_my_service.search import BM25Index, Retriever
from find_my_service.usage import UsageTracker

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(
    settings: Settings | None = None,
    retriever: Retriever | None = None,
    assistant: Assistant | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    retriever = retriever or BM25Index.from_file(settings.corpus_path)
    usage = UsageTracker(
        settings.price_input_per_mtok, settings.price_output_per_mtok, settings.daily_budget_usd
    )
    assistant = assistant or Assistant(settings, retriever, usage)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    metrics = Metrics()

    app = FastAPI(title="Find My Service", version="0.1.0", docs_url="/docs", redoc_url=None)
    app.state.assistant = assistant
    app.state.metrics = metrics

    @app.middleware("http")
    async def add_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = request_id.set(request.headers.get("x-request-id") or uuid.uuid4().hex[:12])
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id.get()
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            return response
        finally:
            request_id.reset(token)

    def guard(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(client)
        if not allowed:
            metrics.increment("rate_limited")
            raise HTTPException(
                status_code=429,
                detail="Too many questions. Please wait a minute and try again.",
                headers={"Retry-After": str(retry_after)},
            )

    def run(question: str) -> Answer:
        if len(question) > settings.max_question_chars:
            raise HTTPException(
                status_code=422,
                detail=f"Questions must be {settings.max_question_chars} characters or fewer.",
            )
        answer = assistant.ask(question)
        metrics.increment(
            "questions", f"outcome:{answer.outcome.value}", f"provider:{answer.provider}"
        )
        return answer

    @app.post("/api/ask", response_model=Answer, response_model_exclude={"flags"})
    def ask(body: AskRequest, request: Request) -> Answer:
        guard(request)
        return run(body.question)

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request) -> HTMLResponse:
        return _render(request, settings)

    @app.post("/", response_class=HTMLResponse)
    def page_submit(request: Request, question: Annotated[str, Form()] = "") -> HTMLResponse:
        question = question.strip()
        if not question:
            return _render(
                request, settings, question=question, error="Enter your question", status=400
            )
        if len(question) > settings.max_question_chars:
            return _render(
                request,
                settings,
                question=question,
                status=400,
                error=f"Your question must be {settings.max_question_chars} characters or fewer",
            )
        try:
            guard(request)
        except HTTPException as exc:
            return _render(
                request, settings, question=question, error=str(exc.detail), status=exc.status_code
            )
        return _render(request, settings, question=question, answer=run(question))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        chunks = len(getattr(retriever, "chunks", []))
        ok = chunks > 0
        return JSONResponse(
            {
                "status": "ready" if ok else "not_ready",
                "chunks": chunks,
                "ai": assistant.ai.name if assistant.ai else "none",
            },
            status_code=200 if ok else 503,
        )

    @app.get("/metrics")
    def metrics_view() -> dict[str, Any]:
        return {"counts": metrics.snapshot(), "ai_usage": assistant.usage.snapshot()}

    return app


def _render(
    request: Request,
    settings: Settings,
    question: str = "",
    answer: Answer | None = None,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "council": settings.council_name,
            "question": question,
            "answer": answer,
            "error": error,
            "max_chars": settings.max_question_chars,
        },
        status_code=status,
    )


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("find_my_service.app:create_app", factory=True, host="0.0.0.0", port=8000)  # noqa: S104
