from __future__ import annotations

from fastapi.testclient import TestClient

from find_my_service.app import create_app
from find_my_service.config import Settings
from find_my_service.search import BM25Index


def client(settings: Settings, index: BM25Index) -> TestClient:
    return TestClient(create_app(settings=settings, retriever=index))


def test_api_answers_with_sources(settings: Settings, index: BM25Index) -> None:
    response = client(settings, index).post("/api/ask", json={"question": "When do bins go out?"})
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "answered"
    assert body["sources"][0]["url"].endswith("/bins/collection-day")
    assert "flags" not in body
    assert response.headers["X-Request-ID"]


def test_api_rejects_long_questions(settings: Settings, index: BM25Index) -> None:
    response = client(settings, index).post("/api/ask", json={"question": "x" * 501})
    assert response.status_code == 422


def test_rate_limit(settings: Settings, index: BM25Index) -> None:
    c = client(settings.model_copy(update={"rate_limit_per_minute": 2}), index)
    codes = [c.post("/api/ask", json={"question": "bins"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_page_works_without_javascript(settings: Settings, index: BM25Index) -> None:
    c = client(settings, index)
    page = c.get("/")
    assert page.status_code == 200
    assert '<label for="question">' in page.text
    answer = c.post("/", data={"question": "Which bin for glass jars?"})
    assert answer.status_code == 200
    assert "Glass bottles and jars" in answer.text
    assert "council.example/bins/what-goes" in answer.text


def test_page_shows_error_for_empty_question(settings: Settings, index: BM25Index) -> None:
    response = client(settings, index).post("/", data={"question": "  "})
    assert response.status_code == 400
    assert "There is a problem" in response.text
    assert "<title>Error:" in response.text


def test_health_ready_and_metrics(settings: Settings, index: BM25Index) -> None:
    c = client(settings, index)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").json()["chunks"] == 4
    c.post("/api/ask", json={"question": "When do bins go out?"})
    metrics = c.get("/metrics").json()
    assert metrics["counts"]["questions"] == 1
    assert metrics["counts"]["outcome:answered"] == 1
