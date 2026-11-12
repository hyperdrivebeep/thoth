import { Classes } from "@blueprintjs/core";
import { createContext } from "react";

/** The id of the tab list at the top of the workspace (conversation, sources, history, trace, settings). */
export const WORKSPACE_TABS_ID = "workspace-tabs";

/** The page the workspace tabs show now. The content below the tabs is that tab's panel. */
export const WorkspacePageContext = createContext<string | null>(null);

/**
 * The ids Blueprint gives a tab and expects of its panel. The tabs are drawn without panels because the
 * page content lives in other cells of the screen grid, so the content names itself with the same ids
 * (Blueprint Tabs documentation, "TabPanel": a panel rendered elsewhere in the DOM).
 */
export function workspaceTabIds(page: string): { tab: string; panel: string } {
  return {
    tab: Classes.TAB + "-title_" + WORKSPACE_TABS_ID + "_" + page,
    panel: Classes.TAB_PANEL + "_" + WORKSPACE_TABS_ID + "_" + page,
  };
}
