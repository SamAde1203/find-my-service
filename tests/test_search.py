from __future__ import annotations

from find_my_service.search import BM25Index, stem, tokenize


def test_tokenize_drops_stopwords_and_maps_synonyms() -> None:
    assert tokenize("When is my rubbish collected?") == tokenize("waste collection")
    assert "when" not in tokenize("When is my rubbish collected?")


def test_stem_matches_plurals() -> None:
    assert stem("collections") == stem("collection")
    assert stem("potholes") == stem("pothole")
    assert stem("enforces") == stem("enforcing")


def test_best_section_ranks_first(index: BM25Index) -> None:
    hits = index.search("what time should bins go out", 3)
    assert hits[0].chunk.id == "1"


def test_synonyms_find_the_right_page(index: BM25Index) -> None:
    hits = index.search("is it cheaper if I live alone", 3)
    assert hits and hits[0].chunk.id == "3"


def test_unrelated_question_finds_nothing(index: BM25Index) -> None:
    assert index.search("recommend an Italian restaurant", 3) == []


def test_coverage_counts_shared_words(index: BM25Index) -> None:
    hit = index.search("glass jars", 1)[0]
    assert hit.matched == 2
    assert hit.coverage == 1.0
