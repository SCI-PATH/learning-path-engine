"""
Write OpenAPI (Swagger) JSON for the Learning Path Engine API.

Run from repo root or backend:
  python scripts/export_openapi.py

Output: backend/openapi.json (same contract as GET /openapi.json when the server runs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
OUT = BACKEND_ROOT / "openapi.json"


def main() -> None:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.main import app  # noqa: E402

    spec = app.openapi()
    OUT.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
