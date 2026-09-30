import { useEffect, useRef, useState } from 'react';

import type { Alert } from '../types';

interface Props {
  alerts: Alert[];
  onAlertClick: (alert: Alert) => boolean;
}

const MAX_TOASTS = 3;
const DISMISS_MS = 9000;

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return '';
  }
}

// Live arrivals pop as cards on the right (P3): only alerts that land AFTER
// mount toast — the initial history load is marked seen silently, so opening
// the app never storms. Each toast auto-dismisses; click jumps to the node.
export default function AlertStack({ alerts, onAlertClick }: Props) {
  const [toasts, setToasts] = useState<Alert[]>([]);
  const seenRef = useRef<Set<number> | null>(null);
  const timersRef = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    if (seenRef.current === null) {
      seenRef.current = new Set(alerts.map((a) => a.id));
      return;
    }
    const fresh = alerts.filter((a) => !seenRef.current!.has(a.id));
    if (fresh.length === 0) return;
    for (const a of fresh) seenRef.current!.add(a.id);
    setToasts((prev) => [...fresh.reverse(), ...prev].slice(0, MAX_TOASTS));
    for (const a of fresh) {
      if (timersRef.current.has(a.id)) continue;
      timersRef.current.set(
        a.id,
        setTimeout(() => {
          timersRef.current.delete(a.id);
          setToasts((prev) => prev.filter((t) => t.id !== a.id));
        }, DISMISS_MS),
      );
    }
  }, [alerts]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const t of timers.values()) clearTimeout(t);
      timers.clear();
    };
  }, []);

  const dismiss = (id: number) => {
    const t = timersRef.current.get(id);
    if (t) {
      clearTimeout(t);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((x) => x.id !== id));
  };

  // Clicking navigates first: the toast dismisses only when the click
  // landed somewhere (node revealed). A missed navigation keeps the toast
  // (it still auto-dismisses) and opens the alerts drawer for context.
  const activate = (a: Alert) => {
    if (onAlertClick(a)) dismiss(a.id);
  };

  if (toasts.length === 0) return null;
  return (
    <div className="alert-stack" role="status" aria-label="New alerts">
      {toasts.map((a) => (
        <div
          key={a.id}
          className="alert-toast"
          onClick={() => activate(a)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              activate(a);
            }
          }}
        >
          <div className="alert-toast-head">
            <span className="alert-toast-badge">● alert</span>
            <span className="alert-toast-time">{fmtTime(a.created_at)}</span>
            <button
              className="alert-toast-close"
              aria-label="Dismiss alert"
              onClick={(e) => {
                e.stopPropagation();
                dismiss(a.id);
              }}
            >
              ×
            </button>
          </div>
          <div className="alert-toast-title">{a.title}</div>
          <div className="alert-toast-summary">{a.summary}</div>
        </div>
      ))}
    </div>
  );
}
