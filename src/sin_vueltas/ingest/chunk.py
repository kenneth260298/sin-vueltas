"""Split approved document text into chunks for search.

Chunks follow the document's sections: every Markdown heading starts a new chunk,
so an FAQ answer or a requirements list stays together and is labeled with its
heading path (for example "Licencias > Duplicado"). Numbered FAQ questions
("14. ¿Qué datos...?"), common in PDFs that have no Markdown headings, also start a
new chunk, so a question is never separated from its answer. Table-of-contents
lines (ending in a page number) are dropped. Long sections are packed into several
chunks up to a size limit; a block that is too long on its own is split by lines,
then by sentences.
"""

import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 1200  # About 300 tokens of Spanish text.

HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
# A numbered question, such as "14. ¿Qué datos se tienen que poner?".
QUESTION = re.compile(r"^\s*\d{1,3}[.)]\s*¿")
# Questions nest under every Markdown heading level.
QUESTION_LEVEL = 7
# How many extra lines a question that wraps in a PDF may take to reach its "?".
MAX_QUESTION_LINES = 3
SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")
# A table-of-contents entry: text, a leader and a page number ("Inscripción …17").
TOC_ENTRY = re.compile(r"(?:…|\.{2,}|\s\.)\s*\d{1,3}\s*$")
# Form fill-in leaders, such as "Nombre: ..........." or "Firma: ________".
LEADERS = re.compile(r"[ \t]*(?:[._][ \t]?){5,}")
EMPHASIS = re.compile(r"\*\*|__|`")


@dataclass(frozen=True)
class Chunk:
    position: int
    heading: str | None
    content: str


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split text into section-aware chunks of at most `max_chars` characters."""
    chunks: list[Chunk] = []
    parts: list[str] = []
    size = 0
    has_body = False
    path: list[tuple[int, str]] = []  # (level, title) of the headings above.

    def label() -> str | None:
        return " > ".join(title for _, title in path) or None

    chunk_label = label()

    def flush() -> None:
        nonlocal parts, size, has_body
        if parts:
            chunks.append(Chunk(len(chunks), chunk_label, "\n\n".join(parts)))
        parts, size, has_body = [], 0, False

    for block in _blocks(LEADERS.sub(" …", text), max_chars):
        if heading := _heading(block):
            if has_body or size + len(block) + 2 > max_chars:
                flush()
            level, title = heading
            path = [(lvl, ttl) for lvl, ttl in path if lvl < level]
            path.append((level, title))
            chunk_label = label()
        elif has_body and size + len(block) + 2 > max_chars:
            flush()  # The section continues in a new chunk with the same label.
            chunk_label = label()
        parts.append(block)
        size += len(block) + 2
        has_body = has_body or heading is None
    flush()
    return chunks


def _heading(block: str) -> tuple[int, str] | None:
    """The (level, title) of a Markdown heading or numbered question, else None."""
    if match := HEADING.match(block):
        return len(match.group(1)), EMPHASIS.sub("", match.group(2)).strip()
    if QUESTION.match(block):
        return QUESTION_LEVEL, EMPHASIS.sub("", " ".join(block.split()))
    return None


def _blocks(text: str, max_chars: int) -> list[str]:
    """Paragraph-like blocks, none longer than `max_chars`."""
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        for block in _split_questions(paragraph.strip()):
            if not block:
                continue
            if len(block) <= max_chars:
                blocks.append(block)
            else:
                blocks.extend(_split_long(block, max_chars))
    return blocks


def _split_questions(paragraph: str) -> list[str]:
    """Give each numbered question its own block and drop table-of-contents lines."""
    blocks: list[str] = []
    current: list[str] = []
    lines = paragraph.splitlines()
    i = 0
    while i < len(lines):
        if QUESTION.match(lines[i]):
            if current:
                blocks.append("\n".join(current))
                current = []
            end = _question_end(lines, i)
            if not TOC_ENTRY.search(lines[end]):
                blocks.append("\n".join(lines[i : end + 1]))
            i = end + 1
            continue
        if not TOC_ENTRY.search(lines[i]):
            current.append(lines[i])
        i += 1
    if current:
        blocks.append("\n".join(current))
    return blocks


def _question_end(lines: list[str], start: int) -> int:
    """The index of the last line of a question that may wrap over several lines."""
    end = start
    while end + 1 < len(lines) and end - start < MAX_QUESTION_LINES:
        line = lines[end].rstrip()
        if line.endswith("?") or TOC_ENTRY.search(line):
            break
        # "¿...? ¿Se bloquea la emisión de" continues on a lowercase line.
        if "?" in line and not lines[end + 1].lstrip()[:1].islower():
            break
        end += 1
    return end


def _split_long(block: str, max_chars: int) -> list[str]:
    """Split a long block by lines, then sentences, then words."""
    pieces: list[str] = []
    for line in block.splitlines():
        if len(line) <= max_chars:
            pieces.append(line)
            continue
        for sentence in SENTENCE_END.split(line):
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                pieces.append(sentence[:cut])
                sentence = sentence[cut:].lstrip()
            pieces.append(sentence)

    # Pack the pieces back together so we do not end up with one chunk per line.
    packed: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > max_chars:
            packed.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        packed.append(current)
    return packed
