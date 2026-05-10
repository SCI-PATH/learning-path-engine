/**
 * Per-lesson "scene" — emoji + gradient for a simple animated card (no external assets).
 * Swap for illustrations / Lottie / Rive later.
 */
const FALLBACK = {
  emoji: "🔬",
  gradient: "linear-gradient(145deg, #e0e7ff 0%, #fce7f3 50%, #fef3c7 100%)",
  caption: "Science time!",
};

const BY_LESSON = {
  g6_sci_01: { emoji: "🌟", gradient: "linear-gradient(145deg, #dbeafe, #fef08a)", caption: "Science & curiosity" },
  g6_sci_02: { emoji: "📏", gradient: "linear-gradient(145deg, #cffafe, #e9d5ff)", caption: "Measure & units" },
  g6_sci_03: { emoji: "⚛️", gradient: "linear-gradient(145deg, #d1fae5, #fed7aa)", caption: "Matter" },
  g6_sci_04: { emoji: "🔥", gradient: "linear-gradient(145deg, #fee2e2, #fde68a)", caption: "Changes" },
  g6_sci_05: { emoji: "🧪", gradient: "linear-gradient(145deg, #e0f2fe, #fce7f3)", caption: "Mixtures" },
  g6_sci_06: { emoji: "🌬️", gradient: "linear-gradient(145deg, #bae6fd, #e0e7ff)", caption: "Air" },
  g6_sci_07: { emoji: "💧", gradient: "linear-gradient(145deg, #a5f3fc, #bfdbfe)", caption: "Water" },
  g6_sci_08: { emoji: "🎢", gradient: "linear-gradient(145deg, #fef9c3, #fecaca)", caption: "Forces" },
  g6_sci_09: { emoji: "⚡", gradient: "linear-gradient(145deg, #fef08a, #fdba74)", caption: "Energy" },
  g6_sci_10: { emoji: "🌈", gradient: "linear-gradient(145deg, #ddd6fe, #fde047)", caption: "Light & sound" },
  g6_sci_11: { emoji: "🌍", gradient: "linear-gradient(145deg, #bbf7d0, #93c5fd)", caption: "Earth" },
};

export function getLessonVisual(lessonId) {
  if (!lessonId) return FALLBACK;
  return BY_LESSON[lessonId] ?? FALLBACK;
}
