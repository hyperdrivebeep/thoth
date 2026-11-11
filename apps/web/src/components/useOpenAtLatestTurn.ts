import { useLayoutEffect, useRef } from "react";

/**
 * Opens a conversation at its latest turn: once its turns are on screen, the last one is brought to
 * the top of the scroll area, so the newest answer is what the person sees first. It happens once
 * per conversation; later refreshes and the person's own scrolling are left alone.
 */
export function useOpenAtLatestTurn(threadId: string, loaded: boolean) {
  const timeline = useRef<HTMLElement>(null);
  const opened = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (!threadId || !loaded || opened.current === threadId) return;
    const turns = timeline.current?.querySelectorAll(".conversation-turn");
    const latest = turns?.[turns.length - 1];
    if (!latest) return;
    opened.current = threadId;
    // jsdom has no layout and no scrollIntoView; a browser always has it.
    latest.scrollIntoView?.({ block: "start" });
  }, [threadId, loaded]);
  return timeline;
}
