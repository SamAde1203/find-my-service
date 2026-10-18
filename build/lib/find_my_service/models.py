"""Data shapes shared across the service."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A section of a council web page: the unit we search and cite."""

    id: str
    url: str
    page_title: str
    heading: str | None = None
    text: str

    @property
    def title(self) -> str:
        if self.heading and self.heading != self.page_title:
            return f"{self.page_title}: {self.heading}"
        return self.page_title


class Source(BaseModel):
    """A chunk returned to the resident as a citation."""

    number: int
    title: str
    url: str
    score: float


class Outcome(StrEnum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"  # Nothing relevant found, or the model could not answer.
    EMERGENCY = "emergency"  # Signposted to emergency help, no search done.
    BLOCKED = "blocked"  # Rejected by an input guardrail.


class Answer(BaseModel):
    outcome: Outcome
    text: str
    sources: list[Source] = Field(default_factory=list)
    provider: str
    # Notes for logs and evaluations, never shown to residents.
    flags: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
