import { Button, Icon, PopoverNext } from "@blueprintjs/core";
import { useState } from "react";
import { threadName } from "../api/threadName";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { WorkThread } from "../types";

function stateLabel(thread: WorkThread): string {
  if (thread.execution_state === "RUNNING") return t("workspace.running");
  if (thread.execution_state === "PAUSED") return t("workspace.paused");
  return t("workspace.reopen");
}

/** One conversation of the open project, with its menu: rename. */
export function ThreadNavItem({ thread, active, onOpen, onRename }: {
  thread: WorkThread;
  active: boolean;
  onOpen: () => void;
  onRename: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const name = threadName(thread) || t("workspace.untitledSession");
  // A rename names the version it changes; a row without one cannot be renamed.
  const canRename = typeof thread.revision === "number";
  const menu = (
    <div className="project-inline-menu" role="menu">
      <Button
        fill
        alignText="left"
        minimal
        role="menuitem"
        icon="edit"
        onClick={() => {
          setMenuOpen(false);
          onRename();
        }}
      >
        {t("manage.rename")}
      </Button>
    </div>
  );
  return (
    <div className={`thread-nav-row ${active ? "active" : ""}`}>
      <Button minimal alignText="left" className={`thread-button ${active ? "active" : ""}`} onClick={onOpen}>
        <Icon icon="chat" />
        <span>
          <strong>{name}</strong>
          <small>{stateLabel(thread)}</small>
        </span>
      </Button>
      {canRename && (
        <PopoverNext isOpen={menuOpen} onInteraction={setMenuOpen} placement="right-start" arrow={false} content={menu}>
          <Button small minimal icon="more" aria-label={message("threadMenu", name)} aria-expanded={menuOpen} />
        </PopoverNext>
      )}
    </div>
  );
}
