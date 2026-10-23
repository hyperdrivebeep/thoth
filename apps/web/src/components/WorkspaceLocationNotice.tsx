import { Callout } from "@blueprintjs/core";
import type { WorkspaceLocationInfo } from "../api/research";
import { useLocale } from "../i18n/locale";

export function WorkspaceLocationNotice({ location }: { location?: WorkspaceLocationInfo }) {
  const locale = useLocale();
  if (!location || !(location.classification === "REMOVABLE"
    || (location.classification === "UNKNOWN" && location.confirmed))) return null;
  return <Callout compact className="workspace-location-notice" intent="warning" role="status">
    {locale === "ko" ? location.message_ko : location.message_en}
  </Callout>;
}
