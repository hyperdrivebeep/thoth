import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import { forgetProject } from "../api/forgetProject";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import type { ProjectSummary } from "../types";
import { ProjectPermanentDelete } from "./ProjectPermanentDelete";
import { useReadAccessProtection } from "./useReadAccessProtection";

/** Archived projects are read-only and hidden from the list; this shows them on demand so one can be restored. */
export function ArchivedProjects({ scopeKey, hosted = false, storageScope, onDeleted }: {
  scopeKey: string;
  /** The hosted review environment cannot delete projects, so it gets no delete button. */
  hosted?: boolean;
  storageScope?: BrowserScope;
  onDeleted?: (projectId: string) => void;
}) {
  useTranslation();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const [deleted, setDeleted] = useState(false);
  const archived = useQuery({ queryKey: ["projects", scopeKey, "archived"], enabled: open,
    queryFn: ({ signal }) => rpc<{ projects: ProjectSummary[] }>("project/list", { project_id: "system:projects", include_archived: true }, crypto.randomUUID(), signal) });
  const restore = useMutation({
    mutationFn: (target: ProjectSummary) => rpc<ProjectSummary>("project/restore", { project_id: target.project_id, expected_revision: target.revision }, crypto.randomUUID()),
    onSuccess: () => client.invalidateQueries({ queryKey: ["projects"] }),
  });
  const blocked = useReadAccessProtection(["projects", scopeKey, "archived"], archived.error);
  const rows = (blocked ? [] : archived.data?.value.projects ?? []).filter(item => item.lifecycle === "ARCHIVED_READ_ONLY");
  const afterDelete = (projectId: string) => {
    client.removeQueries({ predicate: query => query.queryKey.includes(projectId) });
    void client.invalidateQueries({ queryKey: ["projects"] });
    if (storageScope) forgetProject(storageScope, projectId);
    setDeleted(true);
    onDeleted?.(projectId);
  };
  return <div className="archived-projects">
    <Button small minimal icon={open ? "chevron-down" : "box"} aria-expanded={open} onClick={() => setOpen(value => !value)}>{t("archive.show")}</Button>
    {open && <div className="project-sessions">
      {!blocked && archived.isPending && <p className="empty-copy" role="status">{t("archive.loading")}</p>}
      {(archived.error || blocked) && <Callout compact intent="danger" role="alert">{t("archive.failed")}</Callout>}
      {!blocked && archived.isSuccess && rows.length === 0 && <p className="empty-copy">{t("archive.empty")}</p>}
      {rows.map(item => <div className="archived-project-row" key={item.project_id}>
        <span className="archived-project-name" title={item.name}>{item.name}</span>
        <span className="archived-project-actions">
          <Button small minimal icon="reset" aria-label={message("restoreProject", item.name)} loading={restore.isPending && restore.variables?.project_id === item.project_id}
            disabled={restore.isPending} onClick={() => restore.mutate(item)}>{t("archive.restore")}</Button>
          {!hosted && <Button small minimal intent="danger" icon="trash" aria-label={message("deleteProjectLabel", item.name)}
            disabled={restore.isPending} onClick={() => { setDeleted(false); setDeleting(item); }}>{t("archive.delete")}</Button>}
        </span>
      </div>)}
      {!blocked && restore.error && <Callout compact intent="danger" role="alert">{t("archive.restoreFailed")} {restore.error.message}</Callout>}
      {deleted && !deleting && <p className="empty-copy" role="status">{t("archive.deleteDone")}</p>}
    </div>}
    {deleting && <ProjectPermanentDelete target={deleting} onClose={() => setDeleting(null)} onDeleted={afterDelete}/>}
  </div>;
}
