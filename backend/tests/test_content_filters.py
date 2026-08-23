"""Unit checks for lesson-only content filters."""

from app.content_filters import (
    content_type_for_chunk,
    is_non_lesson_chunk,
    polish_generated_lesson,
    scrub_chunk_for_lesson,
    strip_llm_reasoning,
    strip_non_lesson_sections,
)


def test_activity_section_stripped_from_page():
    raw = """
Photosynthesis is the process by which green plants make food.

Activity 1
You will need leaves and water.
Method: Put the leaf in water.

Light is needed for photosynthesis to happen in green leaves every day.
"""
    cleaned = strip_non_lesson_sections(raw)
    assert "Photosynthesis is the process" in cleaned
    assert "You will need" not in cleaned
    assert "Light is needed" in cleaned


def test_theory_with_activity_mention_kept():
    text = (
        "There are living organisms which are visible and also invisible to the naked eye in "
        "our environment. Let us do Activity 1.1 to observe the invisible living organisms. "
        "It is obvious that the unicellular fungal variety called yeast can be observed mainly "
        "in the above sample. This organism cannot be examined to the naked eye in isolation, "
        "but can be observed through a microscope. Therefore, yeast is a microorganism. "
        "The unicellular or multicellular organisms which cannot be observed clearly by naked "
        "eye are called microorganisms. These microorganisms can be observed clearly through "
        "microscopes. Microorganisms are found in every habitat on the earth."
    )
    scrubbed = scrub_chunk_for_lesson(text)
    assert "yeast is a microorganism" in scrubbed
    assert "Let us do Activity" not in scrubbed
    assert content_type_for_chunk(text) == "theory"


def test_exercise_chunk_marked_non_theory():
    text = (
        "Exercise 1 Answer the following questions. "
        "1. What is a magnet? 2. Why do poles attract? "
        "3. Fill in the blanks. True or false: Magnets have two poles. "
        "Choose the correct answer from the options below. "
        "a) North b) South c) Both d) None "
        "You will need a worksheet and a pencil for this classroom task."
    )
    assert is_non_lesson_chunk(text)
    assert content_type_for_chunk(text) == "non_theory"


def test_theory_chunk_kept():
    text = (
        "A magnet is an object that attracts materials such as iron and steel. "
        "Every magnet has two poles called the north pole and the south pole. "
        "Like poles repel each other and unlike poles attract each other. "
        "The Earth also behaves like a huge magnet with magnetic poles. "
        "Magnetic force is strongest near the poles of a magnet."
    )
    assert not is_non_lesson_chunk(text)
    assert content_type_for_chunk(text) == "theory"


def test_microbial_activities_not_rejected():
    text = (
        "The microbial activities change the colour, texture, odour and appearance of food. "
        "The taste and the nutritional value of food also change. Food become unfavourable "
        "for consumption due to the changes of properties. This is known as spoilage of food. "
        "The main reason for food spoilage is the growth of microorganisms on food."
    )
    assert content_type_for_chunk(text) == "theory"


def test_strip_llm_reasoning_removes_planning_prefix():
    raw = """Here's a thinking process:

1. **Analyze User Input:**
* **Role:** King Arthur tutor
* **Constraint 1:** Grounding

2. **Deconstruct Excerpts & Extract Theory/Content:**
* Source 1: Plants have flowers
* Source 2: Underground stems

3. **Structure the Lesson (following constraints):**
Let's draft step-by-step.

*Introduction & Flowering/Non-flowering*

Plants show many different shapes and sizes in our environment.

Some plants produce flowers while others do not.
"""
    out = strip_llm_reasoning(raw)
    assert "thinking process" not in out.lower()
    assert "Analyze User Input" not in out
    assert "Source 1:" not in out
    assert out.startswith("Plants show many different shapes")


def test_polish_generated_lesson_strips_cot():
    raw = "Here's a thinking process:\n\nPlants need sunlight.\n\nLeaves are green."
    out = polish_generated_lesson(raw)
    assert "thinking process" not in out.lower()
    assert "Plants need sunlight." in out
