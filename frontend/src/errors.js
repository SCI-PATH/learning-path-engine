/**
 * User-facing error surface: generic modal + private logging (browser console + POST /client-log).
 */

const apiBase = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ?? "";

/** @type {(open: boolean) => void} */
let setModalOpen = () => {};

/** @type {{ kind: 'generic' | 'offline' } | null} */
let lastKind = null;

export function registerUserErrorModal(setter) {
  setModalOpen = setter;
}

export function getLastErrorKind() {
  return lastKind;
}

/**
 * @param {unknown} error
 * @param {string} context
 * @param {{ userId?: string; offline?: boolean; componentStack?: string }} [options]
 */
export function notifyUserFacingError(error, context, options = {}) {
  const { userId, offline = false, componentStack } = options;
  lastKind = offline ? "offline" : "generic";

  const message = error instanceof Error ? error.message : String(error ?? "Unknown error");
  const detail =
    error instanceof Error && error.stack ? error.stack.slice(0, 4000) : String(error ?? "");

  console.error("[LearningPath client error]", { context, message, error });

  const payload = {
    context: String(context || "unknown").slice(0, 120),
    message: message.slice(0, 2000),
    detail: detail.slice(0, 8000),
    user_id: userId ?? null,
    offline,
    user_agent: typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 512) : "",
    component_stack: componentStack ? componentStack.slice(0, 8000) : null,
  };

  void fetch(`${apiBase}/client-log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {
    /* Logging must never throw */
  });

  setModalOpen(true);
}

export function clearErrorKind() {
  lastKind = null;
}
