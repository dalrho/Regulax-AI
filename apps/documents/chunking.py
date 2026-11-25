import re
from dataclasses import dataclass


CHUNKER_VERSION = 'paragraph-v1'
DEFAULT_CHUNK_CHARS = 1800
_PARAGRAPH_BREAK = re.compile(r'\n[ \t]*\n+')
_MARKDOWN_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+\S.*$')
_NUMBERED_HEADING = re.compile(r'^\s*(?:section\s+)?\d+(?:\.\d+)*(?:[.)])?\s+\S.{0,140}$', re.I)


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    section_heading: str
    chunk_text: str
    start_char: int
    end_char: int


def _trimmed_span(source, start, end):
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return start, end


def _paragraph_spans(source):
    cursor = 0
    for match in _PARAGRAPH_BREAK.finditer(source):
        start, end = _trimmed_span(source, cursor, match.start())
        if start < end:
            yield start, end
        cursor = match.end()
    start, end = _trimmed_span(source, cursor, len(source))
    if start < end:
        yield start, end


def _is_heading(text):
    first_line = text.splitlines()[0].strip()
    if _MARKDOWN_HEADING.match(first_line) or _NUMBERED_HEADING.match(first_line):
        return True
    if len(text.splitlines()) == 1 and len(first_line) <= 100 and any(char.isalpha() for char in first_line):
        letters = [char for char in first_line if char.isalpha()]
        return len(letters) >= 3 and sum(char.isupper() for char in letters) / len(letters) >= 0.8
    return False


def _split_long_span(source, start, end, max_chars):
    while end - start > max_chars:
        cut = start + max_chars
        whitespace = source.rfind(' ', start + max_chars // 2, cut)
        newline = source.rfind('\n', start + max_chars // 2, cut)
        boundary = max(whitespace, newline)
        if boundary <= start:
            boundary = cut
        part_start, part_end = _trimmed_span(source, start, boundary)
        if part_start < part_end:
            yield part_start, part_end
        start = boundary
        while start < end and source[start].isspace():
            start += 1
    part_start, part_end = _trimmed_span(source, start, end)
    if part_start < part_end:
        yield part_start, part_end


def chunk_text(source, *, max_chars=DEFAULT_CHUNK_CHARS):
    """Create deterministic paragraph-aligned source slices with stable offsets."""
    if not isinstance(source, str):
        raise TypeError('Canonical SOP text must be a string.')
    if max_chars < 1:
        raise ValueError('max_chars must be positive.')

    specs = []
    pending_start = None
    pending_end = None
    pending_heading = ''
    active_heading = ''

    def flush():
        nonlocal pending_start, pending_end
        if pending_start is None:
            return
        specs.append(ChunkSpec(
            chunk_index=len(specs),
            section_heading=pending_heading,
            chunk_text=source[pending_start:pending_end],
            start_char=pending_start,
            end_char=pending_end,
        ))
        pending_start = pending_end = None

    for para_start, para_end in _paragraph_spans(source):
        paragraph = source[para_start:para_end]
        heading = _is_heading(paragraph)
        if heading:
            flush()
            active_heading = paragraph.splitlines()[0].strip().lstrip('#').strip()[:500]

        if para_end - para_start > max_chars:
            flush()
            for start, end in _split_long_span(source, para_start, para_end, max_chars):
                specs.append(ChunkSpec(
                    chunk_index=len(specs), section_heading=active_heading,
                    chunk_text=source[start:end], start_char=start, end_char=end,
                ))
            continue

        if pending_start is None:
            pending_start, pending_end, pending_heading = para_start, para_end, active_heading
        elif para_end - pending_start <= max_chars:
            pending_end = para_end
        else:
            flush()
            pending_start, pending_end, pending_heading = para_start, para_end, active_heading

    flush()
    return specs
