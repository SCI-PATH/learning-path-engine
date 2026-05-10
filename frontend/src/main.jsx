import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import UserErrorModal from "./components/UserErrorModal.jsx";
import { notifyUserFacingError, registerUserErrorModal } from "./errors.js";
import "./index.css";

function Shell() {
  const [errorModalOpen, setErrorModalOpen] = useState(false);

  useEffect(() => {
    registerUserErrorModal(setErrorModalOpen);
  }, []);

  useEffect(() => {
    function onUnhandledRejection(ev) {
      ev.preventDefault();
      notifyUserFacingError(ev.reason ?? new Error(String(ev.reason)), "unhandledrejection", {});
    }
    function onWindowError(ev) {
      if (ev.target && ev.target !== window) return;
      notifyUserFacingError(ev.error ?? new Error(ev.message || "window-error"), "window-error", {});
    }
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    window.addEventListener("error", onWindowError);
    return () => {
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
      window.removeEventListener("error", onWindowError);
    };
  }, []);

  return (
    <React.StrictMode>
      <UserErrorModal open={errorModalOpen} onClose={() => setErrorModalOpen(false)} />
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<Shell />);
