"""
Run from the backend folder:

  .\\.venv\\Scripts\\activate
  python scripts\\ingest.py              # all grades G6–G9
  python scripts\\ingest.py --grades 7 8 9
  python scripts\\ingest.py --pdf \"data\\textbooks\\science G-7 P-I E.pdf\"

Ingests textbooks into Chroma under backend\\data\\chroma with chapter-scoped theory chunks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.textbook_ingest import (  # noqa: E402
    DEFAULT_PDF_PATH,
    DEFAULT_PDF_PATHS,
    collection_stats,
    run_ingest,
    run_ingest_many,
)

# Ordered lesson-only ingest paths by grade (Part I then Part II).
GRADE_PDFS: dict[int, tuple[Path, ...]] = {
    6: (DEFAULT_PDF_PATHS[0],),
    7: (DEFAULT_PDF_PATHS[1], DEFAULT_PDF_PATHS[2]),
    8: (DEFAULT_PDF_PATHS[3], DEFAULT_PDF_PATHS[4]),
    9: (DEFAULT_PDF_PATHS[5], DEFAULT_PDF_PATHS[6]),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Grade 6–9 Science PDFs into Chroma (chapter-scoped theory chunks)."
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        action="append",
        default=None,
        help="PDF path (repeatable). Default: all known grade PDFs at repo root.",
    )
    parser.add_argument(
        "--grades",
        type=int,
        nargs="+",
        choices=[6, 7, 8, 9],
        default=None,
        help="Ingest only these grades in order (e.g. --grades 7 8 9). Part I then Part II.",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not delete existing collection first (may duplicate ids if same file).",
    )
    parser.add_argument(
        "--whole-book",
        action="store_true",
        help="Single topic_id for all chunks (legacy). Default: chapter-scoped via curriculum.json.",
    )
    parser.add_argument(
        "--include-non-theory",
        action="store_true",
        help="Include activity/background/practical chunks. Default keeps only theory chunks.",
    )
    parser.add_argument(
        "--g6-only",
        action="store_true",
        help="Only ingest the Grade 6 PDF (legacy default path).",
    )
    args = parser.parse_args()

    clear = not args.no_clear
    chapter_scoped = not args.whole_book
    theory_only = not args.include_non_theory

    if args.g6_only:
        n = run_ingest(
            DEFAULT_PDF_PATH,
            clear_collection=clear,
            chapter_scoped=chapter_scoped,
            theory_only=theory_only,
        )
        print(f"Ingested {n} chunks from {DEFAULT_PDF_PATH.name}")
    elif args.grades:
        paths: list[Path] = []
        for g in args.grades:
            paths.extend(GRADE_PDFS[g])
        print(
            f"Ingesting grades {args.grades} ({len(paths)} PDFs), "
            f"lesson/theory only, chapter-scoped..."
        )
        results = run_ingest_many(
            paths,
            clear_collection=clear,
            chapter_scoped=chapter_scoped,
            theory_only=theory_only,
        )
        for name, n in results.items():
            print(f"  {name}: {n} chunks")
        print(f"Ingested {sum(results.values())} chunks total.")
    elif args.pdf:
        results = run_ingest_many(
            args.pdf,
            clear_collection=clear,
            chapter_scoped=chapter_scoped,
            theory_only=theory_only,
        )
        for name, n in results.items():
            print(f"  {name}: {n} chunks")
        print(f"Ingested {sum(results.values())} chunks total.")
    else:
        print(f"Ingesting {len(DEFAULT_PDF_PATHS)} textbooks (theory-only, chapter-scoped)...")
        results = run_ingest_many(
            DEFAULT_PDF_PATHS,
            clear_collection=clear,
            chapter_scoped=chapter_scoped,
            theory_only=theory_only,
        )
        for name, n in results.items():
            print(f"  {name}: {n} chunks")
        print(f"Ingested {sum(results.values())} chunks total.")

    stats = collection_stats()
    print(f"Chroma collection {stats['name']!r} now has {stats['count']} documents.")


if __name__ == "__main__":
    main()
