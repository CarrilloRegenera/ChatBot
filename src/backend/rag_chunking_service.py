from typing import Callable, Dict, List


def find_chunk_boundary(
    text: str,
    *,
    chunk_size: int,
    grace: int,
    min_chunk_length: int,
) -> int:
    if len(text) <= chunk_size:
        return len(text)

    forward_limit = min(len(text), chunk_size + grace)

    # Prioridad maxima: doble salto de linea como limite de parrafo.
    for idx in range(chunk_size, forward_limit - 1):
        if text[idx] == "\n" and text[idx + 1] == "\n":
            return idx + 2

    for idx in range(chunk_size, forward_limit):
        if text[idx] in ".;!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
            return idx + 1

    backward_limit = max(min_chunk_length, chunk_size - grace)
    for idx in range(chunk_size - 1, backward_limit - 1, -1):
        if text[idx] in ".;!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
            return idx + 1

    for idx in range(chunk_size, min(len(text), chunk_size + 80)):
        if text[idx].isspace():
            return idx

    for idx in range(chunk_size - 1, max(min_chunk_length, chunk_size - 80) - 1, -1):
        if text[idx].isspace():
            return idx

    return chunk_size


def split_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    chunk_sentence_grace: int,
    min_chunk_length: int,
    extract_text_blocks: Callable[[str], List[Dict[str, str]]],
    format_chunk: Callable[[str, str], str],
    find_chunk_boundary_fn: Callable[..., int],
) -> List[str]:
    blocks = extract_text_blocks(text)
    if not blocks:
        return []

    chunks = []
    current = ""
    current_section = ""

    for block in blocks:
        block_text = block["text"]
        block_section = block["section"]
        candidate = f"{current} {block_text}".strip() if current else block_text
        if len(candidate) <= chunk_size:
            current = candidate
            current_section = current_section or block_section
            continue

        if current and len(current) >= min_chunk_length:
            chunks.append(format_chunk(current, current_section))

        overlap_tail = current[-overlap:].strip() if current else ""
        current = f"{overlap_tail} {block_text}".strip() if overlap_tail else block_text
        current_section = block_section

        while len(current) > chunk_size:
            boundary = find_chunk_boundary_fn(
                current,
                chunk_size=chunk_size,
                grace=chunk_sentence_grace,
                min_chunk_length=min_chunk_length,
            )
            partial = current[:boundary].strip()
            if len(partial) >= min_chunk_length:
                chunks.append(format_chunk(partial, current_section))
            next_start = max(boundary - overlap, 1)
            current = current[next_start:].strip()

    if current and len(current) >= min_chunk_length:
        chunks.append(format_chunk(current, current_section))

    return chunks
