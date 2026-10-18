"""The question-answering pipeline.

question
  -> input guardrails (emergency, prompt injection, personal details)
  -> search the council's pages
  -> nothing relevant?  say so, and point to the contact page
  -> generate an answer from the top sources (Claude, or extractive)
  -> output guardrails (citations, links); fall back to extractive if they fail
  -> answer with numbered sources
"""

from __future__ import annotations

import logging
import time

from find_my_service.config import Settings
from find_my_service.guardrails import check_input, check_output
from find_my_service.models import Answer, Outcome, Source
from find_my_service.providers import (
    AnthropicProvider,
    ExtractiveProvider,
    Generation,
    Provider,
)
from find_my_service.search import Hit, Retriever
from find_my_service.usage import UsageTracker

log = logging.getLogger("find_my_service")

EMERGENCY_TEXT = (
    "If you or someone else is in immediate danger, call 999 now. "
    "If you smell gas, leave the building and call the National Gas Emergency Service "
    "on 0800 111 999. If you are struggling and need to talk, you can call Samaritans "
    "free on 116 123, at any time."
)
BLOCKED_TEXT = (
    "I can only answer questions about {council} services. "
    "Please ask your question in a different way."
)
NO_ANSWER_TEXT = (
    "I could not find an answer to that on the {council} website. "
    "You can phone us on {phone}, Monday to Friday, 9am to 5pm."
)


class Assistant:
    def __init__(
        self,
        settings: Settings,
        retriever: Retriever,
        usage: UsageTracker,
        ai: Provider | None = None,
    ) -> None:
        self.settings = settings
        self.retriever = retriever
        self.usage = usage
        self.extractive = ExtractiveProvider(weight=getattr(retriever, "weight", None))
        self.ai = ai if ai is not None else self._make_ai(settings)

    @staticmethod
    def _make_ai(settings: Settings) -> Provider | None:
        if settings.provider == "extractive":
            return None
        if settings.anthropic_api_key:
            return AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.model,
                max_tokens=settings.max_output_tokens,
                timeout=settings.request_timeout_seconds,
                council=settings.council_name,
            )
        if settings.provider == "anthropic":
            raise ValueError("FMS_PROVIDER=anthropic needs ANTHROPIC_API_KEY to be set")
        return None

    def ask(self, question: str) -> Answer:
        started = time.perf_counter()
        answer = self._answer(question.strip()[: self.settings.max_question_chars])
        log.info(
            "answered",
            extra={
                "outcome": answer.outcome.value,
                "provider": answer.provider,
                "sources": [s.url for s in answer.sources],
                "flags": answer.flags,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return answer

    def _answer(self, question: str) -> Answer:
        council = self.settings.council_name
        checked = check_input(question)
        flags = [f"redacted:{name}" for name in checked.redactions]

        if checked.emergency:
            return Answer(
                outcome=Outcome.EMERGENCY,
                text=EMERGENCY_TEXT,
                provider="rules",
                flags=[*flags, "emergency"],
            )
        if checked.injection:
            return Answer(
                outcome=Outcome.BLOCKED,
                text=BLOCKED_TEXT.format(council=council),
                provider="rules",
                flags=[*flags, "prompt_injection"],
            )

        # Keep sections that score well AND share two words (or half the words)
        # with the question, so one coincidental word is not treated as a match.
        hits = [
            h
            for h in self.retriever.search(checked.search_text, self.settings.top_k)
            if h.score >= self.settings.min_score
            and (h.matched >= 2 or h.coverage >= self.settings.min_coverage)
        ]
        if not hits:
            return self._no_answer([*flags, "no_relevant_sources"])

        generation, provider, gen_flags = self._generate(checked.text, hits)
        flags += gen_flags
        if generation.text.strip() == "NO_ANSWER":
            return self._no_answer([*flags, "model_no_answer"], provider)

        allowed = {h.chunk.url for h in hits}
        output = check_output(generation.text, len(hits), allowed)
        if not output.ok and provider != self.extractive.name:
            # The AI broke a rule: use the extractive answer instead.
            flags += [f"output:{p}" for p in output.problems] + ["fallback:extractive"]
            generation = self.extractive.generate(checked.text, hits)
            provider = self.extractive.name
            output = check_output(generation.text, len(hits), allowed)

        cited = output.cited or [1]
        sources = [
            Source(
                number=n,
                title=hits[n - 1].chunk.title,
                url=hits[n - 1].chunk.url,
                score=hits[n - 1].score,
            )
            for n in cited
        ]
        return Answer(
            outcome=Outcome.ANSWERED,
            text=output.text,
            sources=sources,
            provider=provider,
            flags=flags,
        )

    def _generate(self, question: str, hits: list[Hit]) -> tuple[Generation, str, list[str]]:
        if self.ai is None:
            return self.extractive.generate(question, hits), self.extractive.name, []
        if not self.usage.can_spend():
            return (
                self.extractive.generate(question, hits),
                self.extractive.name,
                ["budget_exceeded"],
            )
        try:
            generation = self.ai.generate(question, hits)
        except Exception:
            log.exception("ai_call_failed")
            return self.extractive.generate(question, hits), self.extractive.name, ["ai_error"]
        cost = self.usage.record(generation.input_tokens, generation.output_tokens)
        log.info(
            "ai_call",
            extra={
                "input_tokens": generation.input_tokens,
                "output_tokens": generation.output_tokens,
                "cost_usd": round(cost, 6),
            },
        )
        return generation, self.ai.name, []

    def _no_answer(self, flags: list[str], provider: str = "rules") -> Answer:
        text = NO_ANSWER_TEXT.format(
            council=self.settings.council_name, phone=self.settings.contact_phone
        )
        return Answer(outcome=Outcome.NO_ANSWER, text=text, provider=provider, flags=flags)
