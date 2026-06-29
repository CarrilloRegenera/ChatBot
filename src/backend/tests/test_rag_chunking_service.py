import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_chunking_service import find_chunk_boundary, split_text


def test_find_chunk_boundary_prefers_paragraph_break():
    text = "A" * 10 + "\n\n" + "B" * 20
    boundary = find_chunk_boundary(text, chunk_size=10, grace=10, min_chunk_length=5)
    assert boundary == 12


def test_find_chunk_boundary_falls_back_to_nearby_whitespace():
    text = "palabra " * 20
    boundary = find_chunk_boundary(text, chunk_size=30, grace=5, min_chunk_length=5)
    assert boundary >= 30
    assert boundary <= len(text)


def test_split_text_keeps_sections_and_overlap():
    def extract_blocks(_text):
        return [
            {"text": "alpha beta gamma", "section": "S1"},
            {"text": "delta epsilon zeta eta theta", "section": "S1"},
            {"text": "iota kappa lambda mu", "section": "S2"},
        ]

    def format_chunk(text, section):
        return f"[{section}] {text}"

    chunks = split_text(
        "ignored",
        chunk_size=20,
        overlap=5,
        chunk_sentence_grace=5,
        min_chunk_length=5,
        extract_text_blocks=extract_blocks,
        format_chunk=format_chunk,
        find_chunk_boundary_fn=find_chunk_boundary,
    )

    assert len(chunks) >= 2
    assert chunks[0].startswith("[S1]")
    assert any(chunk.startswith("[S2]") for chunk in chunks)


def test_split_text_returns_empty_when_no_blocks():
    chunks = split_text(
        "",
        chunk_size=20,
        overlap=5,
        chunk_sentence_grace=5,
        min_chunk_length=5,
        extract_text_blocks=lambda _text: [],
        format_chunk=lambda text, section: text,
        find_chunk_boundary_fn=find_chunk_boundary,
    )
    assert chunks == []
