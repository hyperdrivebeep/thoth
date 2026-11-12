import { Callout } from "@blueprintjs/core";
import { projectActionFailure } from "../api/projectActionFailure";
import { useTranslation } from "../i18n/locale";

/** The reason a project action (archive, restore) was refused, in words; nothing when there is no error. */
export function ProjectActionNotice({
  error,
  fallbackKey,
}: {
  error: unknown;
  fallbackKey: "archive.archiveFailed" | "archive.restoreFailed";
}) {
  useTranslation();
  if (!error) return null;
  return (
    <Callout compact intent="danger" role="alert">
      {projectActionFailure(error, fallbackKey)}
    </Callout>
  );
}
