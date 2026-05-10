/** Use same-origin paths in dev (Vite proxy) or set VITE_API_BASE e.g. http://127.0.0.1:8000 */
const apiBase = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ?? "";

async function fetchJson(path, options) {
  const url = `${apiBase}${path}`;
  const res = await fetch(url, options);
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(text || res.statusText);
  }
  if (!res.ok) {
    const detail = data?.detail ?? text;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export function postLesson(body) {
  return fetchJson("/lesson", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getCurriculum() {
  return fetchJson("/curriculum");
}

export function getProgress(userId) {
  const q = new URLSearchParams({ user_id: userId });
  return fetchJson(`/progress?${q}`);
}

export function postProgress(body) {
  return fetchJson("/progress", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getCurrentLesson(userId) {
  const q = new URLSearchParams({ user_id: userId });
  return fetchJson(`/lesson/current?${q}`);
}

export function getHealth() {
  return fetchJson("/health");
}
