import { Button, Icon, Tag } from "@blueprintjs/core";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { sentenceGap } from "../i18n/sentences";
import { NAVIGATION_ID, type NavigationDrawer } from "./useNavigationDrawer";

export type ServerHealth = "ok" | "failed" | "checking";

const healthLabel = (health: ServerHealth) =>
  health === "ok" ? t("workspace.serverConnected") : health === "failed" ? t("workspace.serverFailed") : t("workspace.checkingConnection");
const healthIntent = (health: ServerHealth) => (health === "ok" ? "success" : health === "failed" ? "danger" : "none");

/**
 * The bar above the page: where you are, and what needs attention. Every item stays on one line. When the
 * window is narrow the names give way first (with the whole name in the tooltip) and the stylesheet folds
 * the tags down to their icons, so nothing is stacked one letter per line.
 */
export function WorkspaceTopbar({ drawer, testMode, projectLabel, threadLabel, cutoff, health, setupNeeded, locationUnsaved, missingSelection, onOpenSettings, onRefresh }: {
  drawer: NavigationDrawer;
  testMode: boolean;
  projectLabel: string;
  threadLabel: string | null;
  cutoff: string | null;
  health: ServerHealth;
  setupNeeded: boolean;
  locationUnsaved: boolean;
  missingSelection: "project" | "thread" | null;
  onOpenSettings: () => void;
  onRefresh: () => void;
}) {
  const cutoffText = cutoff ? message("cutoff", cutoff) : null;
  const missingText = missingSelection ? message("missingSelection", missingSelection) : null;
  return (
    <header className="workstation-topbar">
      <Button
        ref={drawer.button}
        className="nav-drawer-button"
        small
        minimal
        icon="menu"
        aria-label={t("workspace.projectList")}
        aria-expanded={drawer.open}
        aria-controls={NAVIGATION_ID}
        onClick={drawer.toggle}
      />
      <div className="context-path">
        <Icon icon="projects" className="context-root" />
        <span className="context-root">{t("nav.workspaceRoot")}</span>
        {testMode && <Tag intent="warning">{t("workspace.testMode")}</Tag>}
        <span className="context-root">/</span>
        <strong title={projectLabel}>{projectLabel}</strong>
        {threadLabel && (
          <>
            <span className="context-thread">/</span>
            <small className="context-thread" title={threadLabel}>{threadLabel}</small>
          </>
        )}
      </div>
      <div className="header-status">
        {cutoffText && (
          <Tag minimal icon="time" className="header-cutoff" title={cutoffText + sentenceGap() + t("workspace.cutoffHint")}>
            {cutoffText}
          </Tag>
        )}
        <Tag minimal className="header-health" data-health={health} intent={healthIntent(health)}>
          {healthLabel(health)}
        </Tag>
        {setupNeeded && (
          <Button small intent="warning" className="setup-needed-button" icon="warning-sign" endIcon="arrow-right"
            title={t("workspace.setupNeededHint")} onClick={onOpenSettings}>
            {t("workspace.setupNeeded")}
          </Button>
        )}
        {locationUnsaved && (
          <Tag minimal intent="warning" className="header-warning" title={t("workspace.locationUnsaved")}>
            {t("workspace.locationUnsaved")}
          </Tag>
        )}
        {missingText && (
          <Tag minimal intent="warning" className="header-warning" title={missingText}>
            {missingText}
          </Tag>
        )}
        <Button small minimal icon="refresh" aria-label={t("workspace.refreshContext")} onClick={onRefresh} />
      </div>
    </header>
  );
}
