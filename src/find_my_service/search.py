"""BM25 keyword search over the chunks.

A council's pages use plain, consistent words ("bin", "Council Tax",
"repair"), so keyword search with light normalisation finds the right
section reliably, runs offline and gives the same result every time,
which keeps the evaluations deterministic. The `Retriever` protocol lets
an embedding search replace it without touching the rest of the service.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import snowballstemmer

from find_my_service.models import Chunk

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "get",
        "got",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "need",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "please",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "up",
        "us",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "should",
        "go",
        "much",
        "many",
        "long",
        "take",
        "tell",
        "want",
        "know",
        "allowed",
        "able",
    ]
)

# Words residents use that the council pages say differently.
SYNONYMS = {
    "rubbish": "waste",
    "trash": "waste",
    "garbage": "waste",
    "bins": "bin",
    "wheelie": "bin",
    "collected": "collection",
    "collect": "collection",
    "emptied": "empty",
    "missed": "miss",
    "wasn": "miss",
    "recycle": "recycling",
    "payment": "pay",
    "paying": "pay",
    "cheaper": "discount",
    "reduction": "discount",
    "flat": "home",
    "property": "home",
    "fix": "repair",
    "broken": "repair",
    "leak": "repair",
    "potholes": "pothole",
    "park": "parking",
    "normal": "routine",
    "ordinary": "routine",
    "decided": "depend",
    "decide": "depend",
    "worked": "depend",
    "calculated": "depend",
    "spend": "spending",
    "spent": "spending",
    "money": "spending",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    tokens = []
    for word in words:
        if word in STOPWORDS:
            continue
        word = SYNONYMS.get(word, word)
        tokens.append(stem(word))
    return tokens


_STEMMER = snowballstemmer.stemmer("english")


def stem(word: str) -> str:
    """Reduces a word to its stem, so "enforces" and "enforcing" match."""
    return str(_STEMMER.stemWord(word))


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    # Share of the question's distinct words found in this chunk (0 to 1).
    coverage: float = 1.0
    # How many of the question's distinct words were found.
    matched: int = 1


class Retriever(Protocol):
    def search(self, query: str, k: int) -> list[Hit]: ...


class BM25Index:
    """Okapi BM25. Titles are counted twice, as they say what a section is about."""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(f"{c.title} {c.title} {c.text}") for c in self.chunks]
        self.lengths = [len(d) for d in self.docs]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.frequencies = [Counter(d) for d in self.docs]
        document_frequency: Counter[str] = Counter()
        for doc in self.docs:
            document_frequency.update(set(doc))
        n = len(self.docs)
        self.idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    @classmethod
    def from_file(cls, path: Path) -> BM25Index:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([Chunk.model_validate(item) for item in data])

    def search(self, query: str, k: int) -> list[Hit]:
        terms = tokenize(query)
        if not terms or not self.chunks:
            return []
        distinct = set(terms)
        scores = []
        for i, freq in enumerate(self.frequencies):
            score = 0.0
            for term in terms:
                tf = freq.get(term, 0)
                if tf == 0:
                    continue
                norm = 1 - self.b + self.b * self.lengths[i] / self.avg_length
                score += self.idf[term] * tf * (self.k1 + 1) / (tf + self.k1 * norm)
            if score > 0:
                matched = len(distinct & freq.keys())
                scores.append((score, i, matched))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [
            Hit(self.chunks[i], round(score, 3), round(matched / len(distinct), 3), matched)
            for score, i, matched in scores[:k]
        ]

    def weight(self, term: str) -> float:
        """How informative a word is: rare words weigh more than common ones."""
        return self.idf.get(term, 0.0)
