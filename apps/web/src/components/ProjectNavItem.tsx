import { Button, Icon, PopoverNext } from "@blueprintjs/core";
import { useState } from "react";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { ProjectSummary } from "../types";

/** One project in the list, with its menu: rename and archive. */
export function ProjectNavItem({ project, active, removing, onOpen, onRename, onRemove }: {
  project: ProjectSummary;
  active: boolean;
  removing: boolean;
  onOpen: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const choose = (action: () => void) => () => {
    setMenuOpen(false);
    action();
  };
  const menu = (
    <div className="project-inline-menu" role="menu">
      <Button fill alignText="left" minimal role="menuitem" icon="edit" onClick={choose(onRename)}>
        {t("manage.rename")}
      </Button>
      <Button fill alignText="left" minimal role="menuitem" icon="box" disabled={removing} onClick={choose(onRemove)}>
        {t("workspace.removeProject")}
      </Button>
    </div>
  );
  return (
    <div className={`project-nav-row ${active ? "active" : ""}`}>
      <Button minimal alignText="left" className={`project-button ${active ? "active" : ""}`} onClick={onOpen}>
        <Icon icon={active ? "folder-open" : "folder-close"} />
        <span>
          <strong>{project.name}</strong>
        </span>
      </Button>
      <PopoverNext isOpen={menuOpen} onInteraction={setMenuOpen} placement="right-start" arrow={false} content={menu}>
        <Button small minimal icon="more" aria-label={message("projectMenu", project.name)} aria-expanded={menuOpen} loading={removing} />
      </PopoverNext>
    </div>
  );
}
