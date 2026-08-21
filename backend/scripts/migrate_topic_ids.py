"""
Migrate content_library topic_id columns to Assessment-style Chroma IDs.

  cd backend
  .\\.venv\\Scripts\\python.exe scripts\\migrate_topic_ids.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.content_library import migrate_topic_ids  # noqa: E402


def main() -> None:
    result = migrate_topic_ids()
    print(f"updated={result['updated']} unchanged={result['unchanged']}")
    for s in result.get("samples") or []:
        print(f"  {s['lesson_id']}: {s['from']} -> {s['to']}")


if __name__ == "__main__":
    main()
