import { useCallback, useLayoutEffect, useRef } from "react";

export type DraftTicket = { scope: string; version: number };

/** Completion may clear only the draft generation that submitted it. */
export function useDraftGeneration(scope: string) {
  const version = useRef(0);
  const currentScope = useRef(scope);
  const active = useRef(true);
  useLayoutEffect(() => {
    currentScope.current = scope;
    active.current = true;
    return () => { active.current = false; version.current += 1; };
  }, [scope]);
  const advance = useCallback(() => { version.current += 1; }, []);
  const capture = useCallback((): DraftTicket => ({ scope, version: version.current }), [scope]);
  const isCurrent = useCallback((ticket: DraftTicket | undefined) => Boolean(ticket && active.current
    && ticket.scope === currentScope.current && ticket.version === version.current), []);
  return { advance, capture, isCurrent };
}
