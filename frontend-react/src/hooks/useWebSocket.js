import { useEffect, useRef, useState } from 'react';
import { connectTaskWS } from '../lib/api';

// Subscribe to /ws/task/{taskId}. Passes events to onEvent.
// Returns connection status + disconnect fn.
export function useTaskWebSocket(taskId, onEvent) {
  const [status, setStatus] = useState('idle');
  const wsRef = useRef(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!taskId) return;
    setStatus('connecting');
    const ws = connectTaskWS(
      taskId,
      (evt) => {
        setStatus('open');
        onEventRef.current?.(evt);
      },
      (closeEvt) => {
        setStatus(closeEvt.code === 4004 ? 'not_found' : 'closed');
      }
    );
    wsRef.current = ws;
    ws.addEventListener('open', () => setStatus('open'));
    return () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, [taskId]);

  return { status, disconnect: () => wsRef.current?.close() };
}
