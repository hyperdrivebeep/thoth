import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { projectActionFailure, rereadProjects } from "../api/projectActionFailure";
import { RpcError, rpc } from "../api/rpcClient";
import { threadName } from "../api/threadName";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { ProjectSummary, WorkThread } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { RenameDialog } from "./RenameDialog";

/** What is being asked, by id: the row itself is looked up again, so a retry uses the version read since. */
type Asking = { kind: "archive" | "renameProject"; projectId: string } | { kind: "renameThread"; threadId: string };

const REVISION_MISMATCH = -32030;

/** Why a conversation could not be renamed, in words. A changed revision means the list on screen was old. */
function threadRenameFailure(error: unknown): string {
  if (error instanceof RpcError && error.code === REVISION_MISMATCH) return t("manage.threadChanged");
  return t("manage.renameFailed");
}

/**
 * Archive and rename for the project list: the three requests, and the window each one asks in.
 * The list component only says which item was chosen; nothing is sent before the person confirms.
 */
export function useProjectManagement({ projects, threads, onArchived }: {
  projects: ProjectSummary[];
  threads: WorkThread[];
  onArchived: (project: ProjectSummary) => void;
}) {
  const client = useQueryClient();
  const [asking, setAsking] = useState<Asking | null>(null);
  const close = () => setAsking(null);

  const archive = useMutation({
    mutationFn: (target: ProjectSummary) =>
      rpc<ProjectSummary>("project/archive", { project_id: target.project_id, expected_revision: target.revision }, crypto.randomUUID()),
    onSuccess: async (_response, target) => {
      close();
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["threads", target.project_id] });
      onArchived(target);
    },
    // A stale revision is the usual reason. Read the list again so the next try uses the current one.
    onError: rereadProjects(client),
  });

  const renameProject = useMutation({
    mutationFn: ({ project, name }: { project: ProjectSummary; name: string }) =>
      rpc<ProjectSummary>(
        "project/metadata/update",
        { project_id: project.project_id, expected_revision: project.revision, name },
        crypto.randomUUID(),
      ),
    onSuccess: async () => {
      close();
      await client.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: rereadProjects(client),
  });

  const renameThread = useMutation({
    mutationFn: ({ thread, name }: { thread: WorkThread; name: string }) =>
      rpc<WorkThread>(
        "thread/metadata/update",
        { project_id: thread.project_id, thread_id: thread.thread_id, expected_revision: thread.revision, display_name: name },
        crypto.randomUUID(),
      ),
    onSuccess: async (_response, { thread }) => {
      close();
      await client.invalidateQueries({ queryKey: ["threads"] });
      await client.invalidateQueries({ queryKey: ["research", thread.project_id, thread.thread_id] });
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: ["threads"] });
    },
  });

  const ask = (next: Asking) => {
    archive.reset();
    renameProject.reset();
    renameThread.reset();
    setAsking(next);
  };

  const project = asking && "projectId" in asking ? projects.find(item => item.project_id === asking.projectId) : undefined;
  const thread = asking && "threadId" in asking ? threads.find(item => item.thread_id === asking.threadId) : undefined;
  let dialog: ReactNode = null;
  if (asking?.kind === "archive" && project) {
    dialog = (
      <ConfirmDialog
        title={t("workspace.removeProject")}
        confirmLabel={t("manage.archiveConfirm")}
        icon="box"
        pending={archive.isPending}
        error={archive.error ? projectActionFailure(archive.error, "archive.archiveFailed") : null}
        onConfirm={() => archive.mutate(project)}
        onClose={close}
      >
        <p>{message("archiveProject", project.name)}</p>
      </ConfirmDialog>
    );
  } else if (asking?.kind === "renameProject" && project) {
    dialog = (
      <RenameDialog
        title={t("manage.renameProjectTitle")}
        current={project.name}
        pending={renameProject.isPending}
        error={renameProject.error ? projectActionFailure(renameProject.error, "manage.renameFailed") : null}
        onSave={name => renameProject.mutate({ project, name })}
        onClose={close}
      />
    );
  } else if (asking?.kind === "renameThread" && thread) {
    dialog = (
      <RenameDialog
        title={t("manage.renameThreadTitle")}
        current={threadName(thread)}
        notice={t("manage.threadNameNotice")}
        pending={renameThread.isPending}
        error={renameThread.error ? threadRenameFailure(renameThread.error) : null}
        onSave={name => renameThread.mutate({ thread, name })}
        onClose={close}
      />
    );
  }

  return {
    dialog,
    archiving: archive.isPending,
    askArchive: (target: ProjectSummary) => ask({ kind: "archive", projectId: target.project_id }),
    askRenameProject: (target: ProjectSummary) => ask({ kind: "renameProject", projectId: target.project_id }),
    askRenameThread: (target: WorkThread) => ask({ kind: "renameThread", threadId: target.thread_id }),
  };
}
