import { BlueprintProvider, Classes } from "@blueprintjs/core";
import type { ReactNode } from "react";

/** The app is dark, but Blueprint draws dialogs, popovers and toasts in a portal outside it. This puts the dark theme on every portal. */
export function ThothBlueprint({ children }: { children: ReactNode }) {
  return <BlueprintProvider portalClassName={Classes.DARK}>{children}</BlueprintProvider>;
}
