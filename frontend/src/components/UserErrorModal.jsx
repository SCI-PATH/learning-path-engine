import { getLastErrorKind, clearErrorKind } from "../errors.js";

export default function UserErrorModal({ open, onClose }) {
  if (!open) return null;

  const kind = getLastErrorKind();

  const title = "Something went wrong";
  const body =
    kind === "offline"
      ? "We could not reach the learning service. Please check your connection, make sure the server is running, and try again in a moment."
      : "Please try again in a little while. If it keeps happening, tell your teacher.";

  function handleClose() {
    clearErrorKind();
    onClose?.();
  }

  return (
    <div className="user-error-modal" role="alertdialog" aria-modal="true" aria-labelledby="user-error-title">
      <div className="user-error-modal__backdrop" onClick={handleClose} aria-hidden />
      <div className="user-error-modal__panel">
        <h2 id="user-error-title" className="user-error-modal__title">
          {title}
        </h2>
        <p className="user-error-modal__body">{body}</p>
        <button type="button" className="user-error-modal__ok" onClick={handleClose}>
          OK
        </button>
      </div>
    </div>
  );
}
