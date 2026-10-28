from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    word_count: int
    page_start: int
    page_end: int


def chunk_pages(
    pages: Sequence[str], chunk_words: int = 300, overlap_words: int = 50
) -> list[TextChunk]:
    """Split page texts into word windows that may span pages.

    Each chunk records the 1-based page where it starts and ends. Consecutive
    chunks share `overlap_words` words, so a sentence cut at a boundary is
    still fully present in one of them.
    """
    if chunk_words <= 0:
        raise ValueError("chunk_words must be positive")
    if not 0 <= overlap_words < chunk_words:
        raise ValueError("overlap_words must be >= 0 and smaller than chunk_words")

    words: list[str] = []
    word_pages: list[int] = []
    for page_number, text in enumerate(pages, start=1):
        tokens = text.split()
        words.extend(tokens)
        word_pages.extend([page_number] * len(tokens))

    chunks: list[TextChunk] = []
    step = chunk_words - overlap_words
    start = 0
    while start < len(words):
        end = min(start + chunk_words, len(words))
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=" ".join(words[start:end]),
                word_count=end - start,
                page_start=word_pages[start],
                page_end=word_pages[end - 1],
            )
        )
        if end == len(words):
            break
        start += step
    return chunks
