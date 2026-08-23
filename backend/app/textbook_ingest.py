"""
Load Grade 6–9 Science PDFs, split into overlapping chunks, store in Chroma.

Metadata:
- topic_id: Chroma filter — Assessment Module / skill hierarchy ID
  (e.g. G7_S1_PLA_DIVER). Shared with Learner Profile Analytics.
- lesson_id: set when chapter-scoped (matches curriculum lesson_id)
- skill_section_id: optional section bucket (e.g. G7_S1)
- skill_topic_ids: comma-separated sibling skill IDs for this chapter
- content_type: "theory" | "non_theory"
- page: PDF page number (1-based)
- chunk_index: chunk order within that page
- source: PDF filename
- grade: optional grade number from curriculum
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader

from app.chroma_setup import COLLECTION_NAME, get_chroma_client, get_textbook_collection
from app.content_filters import clean_page_text, content_type_for_chunk
from app.curriculum import load_curriculum

# Local dev + Docker default: backend/data/textbooks (override with TEXTBOOK_PDF_ROOT).
BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEXTBOOK_DIR = Path(
    os.environ.get("TEXTBOOK_PDF_ROOT", "").strip()
    or str(BACKEND_ROOT / "data" / "textbooks")
)
DEFAULT_PDF_PATH = TEXTBOOK_DIR / "science G-6 E.pdf"

# All known textbooks (ingest-all uses this list).
DEFAULT_PDF_PATHS: tuple[Path, ...] = (
    TEXTBOOK_DIR / "science G-6 E.pdf",
    TEXTBOOK_DIR / "science G-7 P-I E.pdf",
    TEXTBOOK_DIR / "science G-7 P-II E.pdf",
    TEXTBOOK_DIR / "science G8 P-I E.pdf",
    TEXTBOOK_DIR / "science G-8 P-II E.pdf",
    TEXTBOOK_DIR / "science G-9 P-I E.pdf",
    TEXTBOOK_DIR / "Science Part II English G-9.pdf",
)


def chunk_text(text: str, *, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """Split cleaned text into overlapping character windows."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks


def iter_pdf_chunks(
    pdf_path: Path,
    *,
    topic_id: str = "G6_S1_ORG_CHARS",
    chunk_size: int = 1200,
    overlap: int = 200,
    chapter_scoped: bool = True,
    theory_only: bool = True,
) -> Iterable[tuple[str, dict, str]]:
    """
    Yield (document_text, metadata_dict, chunk_id) for each chunk.

    If chapter_scoped is True, topic_id and lesson_id come from curriculum.json
    by (source PDF name + page number).
    """
    curriculum = load_curriculum() if chapter_scoped else None
    reader = PdfReader(str(pdf_path))
    source_name = pdf_path.name
    book = curriculum.book_for_source(source_name) if curriculum else None
    grade = book.grade if book else None

    for page_index, page in enumerate(reader.pages):
        page_num = page_index + 1
        if curriculum:
            scoped_topic, lesson_id = curriculum.topic_id_for_page(
                page_num, source=source_name
            )
        else:
            scoped_topic, lesson_id = topic_id, None
        raw = clean_page_text(page.extract_text() or "")
        for chunk_index, chunk in enumerate(
            chunk_text(raw, chunk_size=chunk_size, overlap=overlap)
        ):
            content_type = content_type_for_chunk(chunk)
            if theory_only and content_type != "theory":
                continue
            # Include source in id so multi-book ingest never collides.
            chunk_id = f"{source_name}::p{page_num}::c{chunk_index}"
            metadata: dict = {
                "topic_id": scoped_topic,
                "content_type": content_type,
                "page": page_num,
                "chunk_index": chunk_index,
                "source": source_name,
            }
            if lesson_id:
                metadata["lesson_id"] = lesson_id
                entry = curriculum.by_lesson_id(lesson_id) if curriculum else None
                if entry:
                    if entry.skill_section_id:
                        metadata["skill_section_id"] = entry.skill_section_id
                    if entry.skill_topic_ids:
                        # Chroma metadata values must be scalars
                        metadata["skill_topic_ids"] = ",".join(entry.skill_topic_ids)
                    if entry.legacy_topic_id:
                        metadata["legacy_topic_id"] = entry.legacy_topic_id
            if grade is not None:
                metadata["grade"] = int(grade)
            yield chunk, metadata, chunk_id


def run_ingest(
    pdf_path: Path | None = None,
    *,
    clear_collection: bool = True,
    batch_size: int = 64,
    chapter_scoped: bool = True,
    theory_only: bool = True,
) -> int:
    """Ingest one PDF into Chroma. Returns number of chunks written."""
    path = pdf_path or DEFAULT_PDF_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"PDF not found: {path}. Place textbooks at repo root or pass pdf_path."
        )

    # Bust curriculum cache so fresh curriculum.json is used after edits.
    load_curriculum.cache_clear()

    collection = get_textbook_collection(reset=clear_collection)
    docs: list[str] = []
    metas: list[dict] = []
    ids: list[str] = []
    count = 0

    for text, meta, cid in iter_pdf_chunks(
        path,
        chapter_scoped=chapter_scoped,
        theory_only=theory_only,
    ):
        docs.append(text)
        metas.append(meta)
        ids.append(cid)
        count += 1
        if len(docs) >= batch_size:
            collection.add(documents=docs, metadatas=metas, ids=ids)
            docs, metas, ids = [], [], []

    if docs:
        collection.add(documents=docs, metadatas=metas, ids=ids)

    return count


def run_ingest_many(
    pdf_paths: Iterable[Path] | None = None,
    *,
    clear_collection: bool = True,
    chapter_scoped: bool = True,
    theory_only: bool = True,
) -> dict[str, int]:
    """
    Ingest multiple PDFs into the same Chroma collection.
    Clears once (if requested), then appends each book.
    """
    paths = [Path(p) for p in (pdf_paths or DEFAULT_PDF_PATHS)]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing PDF(s):\n  " + "\n  ".join(missing))

    load_curriculum.cache_clear()
    results: dict[str, int] = {}
    for i, path in enumerate(paths):
        n = run_ingest(
            path,
            clear_collection=(clear_collection and i == 0),
            chapter_scoped=chapter_scoped,
            theory_only=theory_only,
        )
        results[path.name] = n
    return results


def collection_stats() -> dict:
    client = get_chroma_client()
    try:
        col = client.get_collection(COLLECTION_NAME)
        return {"name": COLLECTION_NAME, "count": col.count()}
    except Exception:
        return {"name": COLLECTION_NAME, "count": 0}
