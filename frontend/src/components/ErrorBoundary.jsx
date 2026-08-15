// Page-level error boundary. Converts an uncaught render error into a readable
// message inside the layout instead of a blank white screen. Resets when the
// user navigates to a different route (keyed on location in Layout).
import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surface for debugging; visible in the browser console.
    console.error("Page render error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card p-6 max-w-2xl">
          <div className="text-lg font-bold text-red-600 mb-1">Something went wrong on this page</div>
          <p className="text-sm text-gray-600 mb-3">
            The page hit an unexpected error while rendering. Your data is safe — try reloading,
            or go back and open the record again. If it keeps happening, share the details below.
          </p>
          <pre className="text-[11px] whitespace-pre-wrap bg-canvas/60 rounded-lg p-3 text-gray-600 max-h-48 overflow-y-auto">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <button className="btn-primary mt-3" onClick={() => window.location.reload()}>
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
