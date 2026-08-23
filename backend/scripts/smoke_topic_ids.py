"""Smoke-test backend after Assessment topic_id ingest."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else None


def post(path: str, payload: dict) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else None


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    try:
        code, stats = get("/debug/chroma-stats")
        check("chroma-stats", code == 200 and (stats or {}).get("count", 0) > 0, str(stats))
    except Exception as exc:
        check("chroma-stats", False, str(exc))
        print("Backend not reachable; aborting further API checks.")
        return 1

    for raw, expect in [
        ("G7_S1_PLA_DIVER", "G7_S1_PLA_DIVER"),
        ("G7_S1_PLA_CLASSIF", "G7_S1_PLA_DIVER"),
        ("g7_science_ch01", "G7_S1_PLA_DIVER"),
        ("g7_sci_01", "G7_S1_PLA_DIVER"),
    ]:
        try:
            code, data = get(f"/curriculum/resolve/{raw}")
            got = (data or {}).get("chroma_topic_id")
            check(f"resolve {raw}", code == 200 and got == expect, f"got={got}")
        except urllib.error.HTTPError as exc:
            check(f"resolve {raw}", False, f"HTTP {exc.code}")
        except Exception as exc:
            check(f"resolve {raw}", False, str(exc))

    try:
        code, cur = get("/curriculum?grade=7")
        lessons = (cur or {}).get("lessons") or []
        first = lessons[0] if lessons else {}
        tid = first.get("topic_id", "")
        check(
            "curriculum grade 7 topic_id",
            code == 200 and bool(lessons) and str(tid).startswith("G7_"),
            f"n={len(lessons)} first={tid}",
        )
    except Exception as exc:
        check("curriculum grade 7 topic_id", False, str(exc))

    # Chroma search with Assessment id
    try:
        code, data = post(
            "/debug/search",
            {
                "query": "diversity of plants classification",
                "topic_id": "G7_S1_PLA_DIVER",
                "top_k": 3,
            },
        )
        hits = (data or {}).get("hits") or []
        meta_tid = (hits[0].get("metadata") or {}).get("topic_id") if hits else None
        check(
            "search Assessment topic_id",
            code == 200 and len(hits) > 0 and meta_tid == "G7_S1_PLA_DIVER",
            f"hits={len(hits)} meta_tid={meta_tid}",
        )
    except Exception as exc:
        check("search Assessment topic_id", False, str(exc))

    # Chroma search with legacy id should still resolve
    try:
        code, data = post(
            "/debug/search",
            {
                "query": "magnets poles",
                "topic_id": "g6_science_ch07",
                "top_k": 3,
            },
        )
        hits = (data or {}).get("hits") or []
        meta_tid = (hits[0].get("metadata") or {}).get("topic_id") if hits else None
        check(
            "search legacy topic_id to new chunks",
            code == 200 and len(hits) > 0 and meta_tid == "G6_S7_MAG_POLES",
            f"hits={len(hits)} meta_tid={meta_tid}",
        )
    except Exception as exc:
        check("search legacy topic_id to new chunks", False, str(exc))

    # lesson_id path
    try:
        code, data = post(
            "/debug/search",
            {
                "query": "electric circuits",
                "lesson_id": "g6_sci_08",
                "top_k": 2,
            },
        )
        hits = (data or {}).get("hits") or []
        meta_tid = (hits[0].get("metadata") or {}).get("topic_id") if hits else None
        check(
            "search by lesson_id",
            code == 200 and len(hits) > 0 and meta_tid == "G6_S8_ELE_CIRCUITS",
            f"hits={len(hits)} meta_tid={meta_tid}",
        )
    except Exception as exc:
        check("search by lesson_id", False, str(exc))

    # teacher library list / student progress smoke
    try:
        code, data = get("/teacher/library?grade=7")
        check("teacher library", code == 200, f"type={type(data).__name__}")
    except Exception as exc:
        check("teacher library", False, str(exc))

    try:
        code, data = get("/progress?user_id=demo-1")
        check("progress", code == 200 and "current_lesson_id" in (data or {}), str((data or {}).get("current_lesson_id")))
    except Exception as exc:
        check("progress", False, str(exc))

    # frontend proxy targets
    for port in (5173,):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                check(f"frontend :{port}", resp.status == 200, f"status={resp.status}")
        except Exception as exc:
            check(f"frontend :{port}", False, str(exc))

    # vite proxy to backend curriculum
    try:
        req = urllib.request.Request("http://127.0.0.1:5173/curriculum?grade=7", method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            lessons = body.get("lessons") or []
            tid = lessons[0].get("topic_id") if lessons else None
            check(
                "frontend proxy /curriculum",
                resp.status == 200 and str(tid or "").startswith("G7_"),
                f"first={tid}",
            )
    except Exception as exc:
        check("frontend proxy /curriculum", False, str(exc))

    print()
    if failures:
        print(f"{len(failures)} failed: {failures}")
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
