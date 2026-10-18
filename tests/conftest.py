from __future__ import annotations

import pytest

from find_my_service.config import Settings
from find_my_service.models import Chunk
from find_my_service.search import BM25Index
from find_my_service.usage import UsageTracker

BASE = "https://council.example"


@pytest.fixture
def chunks() -> list[Chunk]:
    return [
        Chunk(
            id="1",
            url=f"{BASE}/bins/collection-day",
            page_title="Find your bin collection day",
            heading="When to put your bins out",
            text="Put your bins out by 7am on your collection day, with the lid closed. "
            "Bring them back in the same day.",
        ),
        Chunk(
            id="2",
            url=f"{BASE}/bins/what-goes",
            page_title="What goes in each bin",
            heading="Blue bin",
            text="Paper and cardboard. Food and drink cans. Glass bottles and jars.",
        ),
        Chunk(
            id="3",
            url=f"{BASE}/council-tax/discounts",
            page_title="Discounts and exemptions",
            text="You may be able to get a discount if you live on your own (25% off).",
        ),
        Chunk(
            id="4",
            url=f"{BASE}/roads/pothole",
            page_title="Report a pothole",
            text="We inspect dangerous potholes within 24 hours and fix them within "
            "5 working days.",
        ),
    ]


@pytest.fixture
def index(chunks: list[Chunk]) -> BM25Index:
    return BM25Index(chunks)


@pytest.fixture
def settings() -> Settings:
    # BM25 scores are lower in a tiny corpus, so the relevance floor is lower too.
    return Settings(
        provider="extractive", rate_limit_per_minute=1000, anthropic_api_key=None, min_score=0.5
    )


@pytest.fixture
def usage() -> UsageTracker:
    return UsageTracker(price_input_per_mtok=1.0, price_output_per_mtok=5.0, daily_budget_usd=0.01)
