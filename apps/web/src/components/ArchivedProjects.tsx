import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, PopoverNext } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import { rereadProjects } from "../api/projectActionFailure";
import { ProjectActionNotice } from "./ProjectActionNotice";
import { forgetProject } from "../api/forgetProject";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import type { ProjectSummary } from "../types";
import { ProjectPermanentDelete } from "./ProjectPermanentDelete";
import { useReadAccessProtection } from "./useReadAccessProtection";

/**
 * One archived project: its name and the same kind of menu the open projects have. Restoring and deleting
 * sit in the menu, so a permanent delete is not one slip away from a restore.
 */
function ArchivedProjectRow({
  project,
  restoring,
  busy,
  onRestore,
  onDelete,
}: {
  project: ProjectSummary;
  restoring: boolean;
  busy: boolean;
  onRestore: () => void;
  /** Left out where projects cannot be deleted (the hosted review environment). */
  onDelete?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const choose = (action: () => void) => () => {
    setMenuOpen(false);
    action();
  };
  const menu = (
    <div className="project-inline-menu archived-project-menu" role="menu">
      <Button
        fill
        alignText="left"
        minimal
        role="menuitem"
        icon="reset"
        disabled={busy}
        onClick={choose(onRestore)}
      >
        {t("archive.restore")}
      </Button>
      {onDelete && (
        <Button
          fill
          alignText="left"
          minimal
          role="menuitem"
          intent="danger"
          icon="trash"
          disabled={busy}
          onClick={choose(onDelete)}
        >
          {t("archive.delete")}
        </Button>
      )}
    </div>
  );
  return (
    <div className="archived-project-row">
      <span className="archived-project-name" title={project.name}>
        {project.name}
      </span>
      <PopoverNext
        isOpen={menuOpen}
        onInteraction={setMenuOpen}
        placement="right-start"
        arrow={false}
        content={menu}
      >
        <Button
          small
          minimal
          icon="more"
          aria-label={message("projectMenu", project.name)}
          aria-expanded={menuOpen}
          loading={restoring}
        />
      </PopoverNext>
    </div>
  );
}

/** Archived projects are read-only and hidden from the list; this shows them on demand so one can be restored. */
export function ArchivedProjects({
  scopeKey,
  hosted = false,
  storageScope,
  onDeleted,
}: {
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
  const archived = useQuery({
    queryKey: ["projects", scopeKey, "archived"],
    enabled: open,
    queryFn: ({ signal }) =>
      rpc<{ projects: ProjectSummary[] }>(
        "project/list",
        { project_id: "system:projects", include_archived: true },
        crypto.randomUUID(),
        signal,
      ),
  });
  const restore = useMutation({
    mutationFn: (target: ProjectSummary) =>
      rpc<ProjectSummary>(
        "project/restore",
        { project_id: target.project_id, expected_revision: target.revision },
        crypto.randomUUID(),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["projects"] }),
    onError: rereadProjects(client),
  });
  const blocked = useReadAccessProtection(["projects", scopeKey, "archived"], archived.error);
  const rows = (blocked ? [] : (archived.data?.value.projects ?? [])).filter(
    (item) => item.lifecycle === "ARCHIVED_READ_ONLY",
  );
  const afterDelete = (projectId: string) => {
    client.removeQueries({ predicate: (query) => query.queryKey.includes(projectId) });
    void client.invalidateQueries({ queryKey: ["projects"] });
    if (storageScope) forgetProject(storageScope, projectId);
    setDeleted(true);
    onDeleted?.(projectId);
  };
  return (
    <div className="archived-projects">
      <Button
        small
        minimal
        icon={open ? "chevron-down" : "box"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {t("archive.show")}
      </Button>
      {open && (
        <div className="project-sessions">
          {!blocked && archived.isPending && (
            <p className="empty-copy" role="status">
              {t("archive.loading")}
            </p>
          )}
          {(archived.error || blocked) && (
            <Callout compact intent="danger" role="alert">
              {t("archive.failed")}
            </Callout>
          )}
          {!blocked && archived.isSuccess && rows.length === 0 && (
            <p className="empty-copy">{t("archive.empty")}</p>
          )}
          {rows.map((item) => (
            <ArchivedProjectRow
              key={item.project_id}
              project={item}
              restoring={restore.isPending && restore.variables?.project_id === item.project_id}
              busy={restore.isPending}
              onRestore={() => restore.mutate(item)}
              onDelete={
                hosted
                  ? undefined
                  : () => {
                      setDeleted(false);
                      setDeleting(item);
                    }
              }
            />
          ))}
          {!blocked && (
            <ProjectActionNotice error={restore.error} fallbackKey="archive.restoreFailed" />
          )}
          {deleted && !deleting && (
            <p className="empty-copy" role="status">
              {t("archive.deleteDone")}
            </p>
          )}
        </div>
      )}
      {deleting && (
        <ProjectPermanentDelete
          target={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={afterDelete}
        />
      )}
    </div>
  );
}
