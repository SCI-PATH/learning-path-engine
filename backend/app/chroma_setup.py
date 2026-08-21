"""Shared Chroma paths and collection name for ingest + API."""

from pathlib import Path

import chromadb

# backend/app/chroma_setup.py -> parents[1] == backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
CHROMA_DIR = BACKEND_ROOT / "data" / "chroma"
# Kept for backward compatibility with existing local Chroma data.
COLLECTION_NAME = "science_grade6_textbook"


def get_chroma_client() -> chromadb.PersistentClient:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_textbook_collection(*, reset: bool = False):
    """
    Return the textbook collection. If reset=True, delete and recreate (empty).
    Holds Grade 6–9 chapter-scoped theory chunks.
    """
    client = get_chroma_client()
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"source": "science_textbooks_g6_g9"},
    )
