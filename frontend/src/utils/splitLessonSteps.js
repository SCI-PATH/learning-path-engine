/**
 * Split LLM lesson text into short "beats" (Duolingo-style: one bite at a time).
 * Targets ~≤200 chars per step; bullets often get their own step.
 */
const DEFAULT_MAX = 200;
const SOFT_MAX = 260;

export function splitLessonIntoSteps(lessonText, maxChars = DEFAULT_MAX) {
  if (!lessonText?.trim()) return [];

  let text = lessonText.trim().replace(/\r\n/g, "\n");
  text = text.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\*([^*]+)\*/g, "$1");
  text = stripLeadingHeading(text);

  const sections = text.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean);
  const steps = [];

  for (const section of sections) {
    const lines = section.split("\n").map((l) => l.trim()).filter(Boolean);
    const isBullet = (l) =>
      /^[-•\u2022]\s+/.test(l) ||
      /^[-*]\s+/.test(l) ||
      /^\d+[.)]\s+/.test(l);

    const bullets = lines.filter(isBullet);
    const nonBullets = lines.filter((l) => !isBullet(l));

    if (bullets.length > 0) {
      if (nonBullets.length > 0) {
        flushParagraph(nonBullets.join(" "), steps, maxChars);
      }
      for (const raw of bullets) {
        const clean = raw.replace(/^[-•\u2022*]\s+/, "").replace(/^\d+[.)]\s+/, "").trim();
        if (clean) flushParagraph(clean, steps, maxChars);
      }
    } else {
      flushParagraph(section.replace(/\n/g, " ").replace(/\s+/g, " ").trim(), steps, maxChars);
    }
  }

  return steps.length ? steps : [text];
}

function stripLeadingHeading(text) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return text;
  const first = lines[0];
  const looksHeading =
    first.length <= 60 &&
    /^[A-Za-z][A-Za-z0-9\s\-']+$/.test(first) &&
    !/[.!?]$/.test(first);
  if (!looksHeading) return text;
  return lines.slice(1).join("\n").trim();
}

function flushParagraph(block, steps, maxChars) {
  if (!block) return;
  if (block.length <= maxChars) {
    steps.push(block);
    return;
  }

  const sentences = splitSentences(block);
  let buf = "";

  function pushBuf() {
    const t = buf.trim();
    if (t) steps.push(t);
    buf = "";
  }

  for (const sentence of sentences) {
    const candidate = buf ? `${buf} ${sentence}`.trim() : sentence.trim();
    if (candidate.length <= maxChars) {
      buf = candidate;
      continue;
    }
    pushBuf();

    if (sentence.length <= maxChars) {
      buf = sentence.trim();
      continue;
    }

    hardChunk(sentence, steps, maxChars);
  }

  pushBuf();

  if (steps.length >= 2) {
    const last = steps[steps.length - 1];
    if (last && last.length < 40 && steps.length >= 2) {
      const prev = steps[steps.length - 2];
      if (prev && `${prev} ${last}`.length <= SOFT_MAX) {
        steps[steps.length - 2] = `${prev} ${last}`.trim();
        steps.pop();
      }
    }
  }
}

function splitSentences(s) {
  const parts = s.split(/(?<=[.!?]["']?)\s+/).filter(Boolean);
  const out = [];
  for (const p of parts) {
    const sub = p.split(/(?<=;)\s+/);
    out.push(...sub);
  }
  return out.filter(Boolean);
}

function hardChunk(sentence, steps, maxChars) {
  const chunks = sentence.split(/(?:,\s+|;\s+)/);
  let buf = "";
  function push() {
    const t = buf.trim();
    if (t) steps.push(t);
    buf = "";
  }
  for (const chunk of chunks) {
    const c = chunk.trim();
    const candidate = buf ? `${buf}${buf.endsWith(",") ? " " : ", "}${c}` : c;
    if (candidate.length <= maxChars) buf = candidate;
    else {
      push();
      if (c.length <= maxChars) buf = c;
      else {
        for (let i = 0; i < c.length; i += maxChars) {
          steps.push(c.slice(i, i + maxChars).trim());
        }
      }
    }
  }
  push();
}
