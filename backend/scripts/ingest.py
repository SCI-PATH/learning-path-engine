"""
Run from the backend folder:

  .\\.venv\\Scripts\\activate
  python scripts\\ingest.py

Ingests ..\\science G-6 E.pdf (repo root) into Chroma under backend\\data\\chroma.
"""

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.textbook_ingest import DEFAULT_PDF_PATH, run_ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Grade 6 Science PDF into Chroma.")
    parser.add_argument(
        "--pdf",
        type=Path,
        default=DEFAULT_PDF_PATH,
        help=f"Path to PDF (default: {DEFAULT_PDF_PATH})",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not delete existing collection first (may duplicate ids if same file).",
    )
    parser.add_argument(
        "--whole-book",
        action="store_true",
        help="Single topic_id g6_science for all chunks (legacy). Default: chapter-scoped via curriculum.json.",
    )
    parser.add_argument(
        "--include-non-theory",
        action="store_true",
        help="Include activity/background/practical chunks. Default keeps only theory chunks.",
    )
    args = parser.parse_args()
    n = run_ingest(
        args.pdf,
        clear_collection=not args.no_clear,
        chapter_scoped=not args.whole_book,
        theory_only=not args.include_non_theory,
    )
    print(f"Ingested {n} chunks into Chroma.")


if __name__ == "__main__":
    main()
