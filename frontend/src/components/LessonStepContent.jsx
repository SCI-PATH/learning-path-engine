/**
 * Renders one lesson "beat" with breathing room — short paragraphs / micro-lists inside the bubble.
 */
export default function LessonStepContent({ text }) {
  if (!text?.trim()) return null;

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const isBulletLine = (l) =>
    /^[-•\u2022]\s+/.test(l) || /^[-*]\s+/.test(l) || /^\d+[.)]\s+/.test(l);

  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBulletLine(line)) {
      const items = [];
      while (i < lines.length && isBulletLine(lines[i])) {
        items.push(
          lines[i]
            .replace(/^[-•\u2022*]\s+/, "")
            .replace(/^\d+[.)]\s+/, "")
            .trim()
        );
        i += 1;
      }
      blocks.push({ type: "ul", items });
    } else {
      const paras = [];
      while (i < lines.length && !isBulletLine(lines[i])) {
        paras.push(lines[i]);
        i += 1;
      }
      if (paras.length) blocks.push({ type: "p", text: paras.join(" ") });
    }
  }

  if (blocks.length === 0) {
    return <p className="lesson-step__p">{text}</p>;
  }

  return (
    <div className="lesson-step">
      {blocks.map((b, idx) =>
        b.type === "ul" ? (
          <ul key={idx} className="lesson-step__ul">
            {b.items.map((item, j) => (
              <li key={j} className="lesson-step__li">
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p key={idx} className="lesson-step__p">
            {b.text}
          </p>
        )
      )}
    </div>
  );
}
