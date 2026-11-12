import { useEffect, useRef, useState } from "react";
import { workflowSocketUrl, type WorkflowEvent } from "./api";

type State = { events: WorkflowEvent[]; status: string | null; connected: boolean };

/**
 * Live workflow events for one request. On connect the server replays every stored event, so a page
 * refresh rebuilds the full timeline; on a dropped connection we reconnect from the last seq seen.
 */
export function useWorkflowEvents(requestId: string | undefined, onEvent?: (e: WorkflowEvent) => void): State {
  const [state, setState] = useState<State>({ events: [], status: null, connected: false });
  const lastSeq = useRef(0);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!requestId) return;
    lastSeq.current = 0;
    setState({ events: [], status: null, connected: false });
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = (attempt = 0) => {
      ws = new WebSocket(workflowSocketUrl(requestId, lastSeq.current));
      ws.onopen = () => setState((s) => ({ ...s, connected: true }));
      ws.onmessage = (msg) => {
        const data = JSON.parse(msg.data);
        if (data.type === "snapshot") {
          setState((s) => ({ ...s, status: data.status }));
        } else if (data.type === "event" && data.seq > lastSeq.current) {
          lastSeq.current = data.seq;
          const ev: WorkflowEvent = { seq: data.seq, event_type: data.event_type, payload: data.payload, created_at: data.created_at };
          setState((s) => ({
            ...s,
            events: [...s.events, ev],
            status: ev.event_type === "workflow.state_changed" ? String(ev.payload.to) : s.status,
          }));
          handler.current?.(ev);
        }
      };
      ws.onclose = (e) => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed && e.code !== 4404) retry = setTimeout(() => connect(attempt + 1), Math.min(5000, 500 * 2 ** attempt));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [requestId]);

  return state;
}
