import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";

export const NAVIGATION_ID = "project-navigation";

/**
 * The project list as a drawer. In a narrow window the stylesheet takes the list out of the page and
 * shows it only while it is open; in a wide window the list is always there and this state changes nothing.
 */
export function useNavigationDrawer() {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const rail = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  const giveFocusBack = useRef(true);

  // Opening moves the focus into the list. Closing gives it back to the button, unless the focus has already moved on.
  useEffect(() => {
    if (open) rail.current?.querySelector<HTMLElement>("button:not([disabled]), input")?.focus();
    else if (wasOpen.current && giveFocusBack.current) button.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const close = useCallback((focusBack = true) => {
    giveFocusBack.current = focusBack;
    setOpen(false);
  }, []);
  const toggle = useCallback(() => {
    giveFocusBack.current = true;
    setOpen((value) => !value);
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    // A menu or a dialog opened from the list closes itself on Escape and does not pass the key on.
    if (event.key !== "Escape" || !open) return;
    event.stopPropagation();
    close();
  };
  const onBlur = (event: FocusEvent<HTMLElement>) => {
    const list = rail.current;
    const next = event.relatedTarget;
    if (!open || !list || !(next instanceof Node) || list.contains(next)) return;
    // Focus that went on into the page leaves the list. A menu or a dialog drawn outside the page does not.
    if (list.parentElement?.contains(next)) close(false);
  };

  /** A choice made in the list (a project, a conversation, the settings): the list closes after it. */
  const closing =
    <A extends unknown[]>(action: (...args: A) => void) =>
    (...args: A) => {
      action(...args);
      close();
    };

  return {
    open,
    toggle,
    close,
    closing,
    button,
    railProps: { id: NAVIGATION_ID, ref: rail, "data-open": open, onKeyDown, onBlur },
    // The dimmed page behind the open list. A click on it closes the list; it is not a control
    // (the keyboard closes the list with Escape or its button), so it is kept out of the reading order.
    backdropProps: {
      className: "nav-drawer-backdrop",
      role: "presentation" as const,
      onClick: () => close(),
    },
  };
}

export type NavigationDrawer = ReturnType<typeof useNavigationDrawer>;
