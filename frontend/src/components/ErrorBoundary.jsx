import React from "react";
import { notifyUserFacingError } from "../errors.js";

/**
 * Catches render errors in the tree below; shows the same friendly modal via notifyUserFacingError.
 */
export default class ErrorBoundary extends React.Component {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    notifyUserFacingError(error, "react-error-boundary", {
      componentStack: info.componentStack,
    });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-fallback">
          <p className="error-boundary-fallback__text">
            Something broke while showing this screen. You can reload the page to continue.
          </p>
          <button type="button" className="error-boundary-fallback__reload" onClick={() => window.location.reload()}>
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
