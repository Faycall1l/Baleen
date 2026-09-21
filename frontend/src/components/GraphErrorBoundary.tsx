import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  failure: string | null;
}

// Catches render-time crashes in the graph subtree and turns a blank canvas
// into a readable error with recovery, instead of silent nothing.
export default class GraphErrorBoundary extends Component<Props, State> {
  state: State = { failure: null };

  static getDerivedStateFromError(err: unknown): State {
    return { failure: err instanceof Error ? err.message : String(err) };
  }

  render(): ReactNode {
    if (this.state.failure) {
      return (
        <div className="empty-hint">
          <div className="error">Graph failed: {this.state.failure}</div>
          <div style={{ marginTop: 8 }}>
            <button onClick={() => window.location.reload()}>Reload</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
