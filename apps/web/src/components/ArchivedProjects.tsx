import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import type { ProjectSummary } from "../types";

/** Archived projects are read-only and hidden from the list; this shows them on demand so one can be restored. */
export function ArchivedProjects({ scopeKey }: { scopeKey: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const archived = useQuery({ queryKey: ["projects", scopeKey, "archived"], enabled: open,
    queryFn: ({ signal }) => rpc<{ projects: ProjectSummary[] }>("project/list", { project_id: "system:projects", include_archived: true }, crypto.randomUUID(), signal) });
  const restore = useMutation({
    mutationFn: (target: ProjectSummary) => rpc<ProjectSummary>("project/restore", { project_id: target.project_id, expected_revision: target.revision }, crypto.randomUUID()),
    onSuccess: () => client.invalidateQueries({ queryKey: ["projects"] }),
  });
  const rows = (archived.data?.value.projects ?? []).filter(item => item.lifecycle === "ARCHIVED_READ_ONLY");
  return <div className="archived-projects">
    <Button small minimal icon={open ? "chevron-down" : "box"} aria-expanded={open} onClick={() => setOpen(value => !value)}>보관한 프로젝트 보기</Button>
    {open && <div className="project-sessions">
      {archived.isPending && <p className="empty-copy" role="status">보관한 프로젝트를 읽는 중…</p>}
      {archived.error && <Callout compact intent="danger" role="alert">보관한 프로젝트를 읽지 못했습니다. 없다는 뜻은 아닙니다.</Callout>}
      {archived.isSuccess && rows.length === 0 && <p className="empty-copy">보관한 프로젝트가 없습니다.</p>}
      {rows.map(item => <div className="archived-project-row" key={item.project_id}>
        <span>{item.name}</span>
        <Button small minimal icon="reset" aria-label={`${item.name} 되살리기`} loading={restore.isPending && restore.variables?.project_id === item.project_id}
          disabled={restore.isPending} onClick={() => restore.mutate(item)}>되살리기</Button>
      </div>)}
      {restore.error && <Callout compact intent="danger" role="alert">되살리지 못했습니다. {restore.error.message}</Callout>}
    </div>}
  </div>;
}
