"""Checks on what goes in to the assistant and what comes out of it.

Input:
- Emergencies are signposted straight away, before any search or AI call.
- Personal details are removed before the question reaches the AI or the logs.
- Obvious prompt-injection attempts are refused.

Output:
- Every answer must cite at least one of the sources it was given, and only
  those sources.
- Links the model invents are removed: residents only see URLs we sent it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Input -----------------------------------------------------------------

EMERGENCY_PATTERNS = [
    r"\b(suicid\w*|kill (my|him|her|them)sel\w*|end (my life|it all)|want to die)\b",
    r"\b(overdose[d]?|can'?t breathe|not breathing|heart attack|stroke)\b",
    r"\b(being (attacked|abused|hurt|followed)|in danger|someone is hurting)\b",
    r"\b(house|flat|home|building|kitchen) (is )?on fire\b|\bthere'?s a fire\b",
    r"\b(gas leak|smell (of )?gas|carbon monoxide)\b",
    r"\b(child|baby|kid)\b.{0,40}\b(abuse\w*|hurt|neglect\w*|unsafe|danger)\b",
]

INJECTION_PATTERNS = [
    r"\bignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)\b",
    r"\b(disregard|forget) (your|the|all) (instructions|rules|guidelines|system prompt)\b",
    r"\b(system prompt|developer message|hidden instructions)\b",
    r"\byou are now\b|\bact as (an? )?(unrestricted|jailbroken|dan)\b",
    r"\bpretend (you are|to be) (?!a resident)",
    r"<\s*/?\s*(system|source|instructions)\s*>",
]

PII_PATTERNS: list[tuple[str, str]] = [
    ("email", r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    ("phone", r"(?<!\d)(?:\+44\s?|0)(?:\d\s?){9,10}(?!\d)"),
    ("ni_number", r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"),
    ("card", r"\b(?:\d[ -]?){13,19}\b"),
    ("council_tax_account", r"\b(?:account|reference|ref)\s*(?:no\.?|number)?\s*[:#]?\s*\d{6,}\b"),
]


@dataclass
class InputCheck:
    text: str
    emergency: bool = False
    injection: bool = False
    redactions: list[str] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        """The question without placeholders like "[email removed]", for searching."""
        return re.sub(r"\[\w+ removed\]", " ", self.text)


def check_input(question: str) -> InputCheck:
    """Classifies a question and removes personal details from it."""
    lowered = question.lower()
    result = InputCheck(text=question)
    result.emergency = any(re.search(p, lowered) for p in EMERGENCY_PATTERNS)
    result.injection = any(re.search(p, lowered) for p in INJECTION_PATTERNS)

    text = question
    for name, pattern in PII_PATTERNS:
        text, count = re.subn(pattern, f"[{name} removed]", text, flags=re.IGNORECASE)
        if count:
            result.redactions.append(name)
    result.text = text
    return result


# --- Output ----------------------------------------------------------------

CITATION = re.compile(r"\[(\d+)\]")
URL = re.compile(r"https?://[^\s)\]>\"']+")


@dataclass
class OutputCheck:
    text: str
    cited: list[int]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_output(text: str, source_count: int, allowed_urls: set[str]) -> OutputCheck:
    """Checks citations and removes links that were not in the sources."""
    problems = []
    cited = sorted({int(n) for n in CITATION.findall(text)})
    if not cited:
        problems.append("no_citation")
    if any(n < 1 or n > source_count for n in cited):
        problems.append("unknown_citation")

    def keep_known(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;:")
        return match.group(0) if url in allowed_urls else "[link removed]"

    cleaned = URL.sub(keep_known, text)
    if "[link removed]" in cleaned:
        problems.append("invented_link")
    return OutputCheck(
        text=cleaned.strip(), cited=[n for n in cited if 1 <= n <= source_count], problems=problems
    )
