"""Golden checks for Assessment-style Chroma topic ID resolution."""

from app.curriculum import load_curriculum
from app.topic_ids import chroma_theory_where, resolve_chroma_topic_id, resolve_lesson


def test_resolve_primary_skill_id():
    assert resolve_chroma_topic_id("G7_S1_PLA_DIVER") == "G7_S1_PLA_DIVER"


def test_resolve_sibling_skill_to_chapter_primary():
    assert resolve_chroma_topic_id("G7_S1_PLA_CLASSIF") == "G7_S1_PLA_DIVER"


def test_resolve_legacy_chroma_id():
    assert resolve_chroma_topic_id("g7_science_ch01") == "G7_S1_PLA_DIVER"


def test_resolve_lesson_id():
    assert resolve_chroma_topic_id("g7_sci_01") == "G7_S1_PLA_DIVER"
    le = resolve_lesson("g6_sci_07")
    assert le is not None
    assert le.topic_id == "G6_S7_MAG_POLES"


def test_chroma_where_uses_primary():
    where = chroma_theory_where("g6_science_ch07")
    # Matches new Assessment id and legacy until Chroma is re-ingested.
    assert where["$and"][1] == {"content_type": "theory"}
    topic_clause = where["$and"][0]
    assert "topic_id" in topic_clause
    ids = topic_clause["topic_id"].get("$in") or [topic_clause["topic_id"]]
    assert "G6_S7_MAG_POLES" in ids
    assert "g6_science_ch07" in ids


def test_curriculum_by_topic_matches_skill_map():
    cur = load_curriculum()
    le = cur.by_topic_id("G6_S8_ELE_CONDINS")
    assert le is not None
    assert le.lesson_id == "g6_sci_08"
    assert cur.resolve_chroma_topic_id("g6_science_ch08") == "G6_S8_ELE_CIRCUITS"
