import { Button, Callout, Collapse, InputGroup } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { captureProjectCreation } from "../api/projectCreation";
import { rpc } from "../api/rpcClient";
import { t, useTranslation } from "../i18n/locale";
import type { ProjectSummary } from "../types";

const localNow = () => {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

/**
 * A new project: its name, then "create". The cutoff and the profile have defaults and sit under a fold.
 * The screen starts with the task; it carries no slogan and no list of product features.
 */
export function ProjectForm({ onCreated }: { onCreated: (projectId: string) => void }) {
  useTranslation();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [overlay, setOverlay] = useState("general-rnd");
  const [cutoff, setCutoff] = useState(localNow);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  type Captured = ReturnType<typeof captureProjectCreation>;
  // One request per name, cutoff and profile: pressing twice sends the same request again, not a second project.
  const pending = useRef<(Captured & { signature: string; inFlight: boolean }) | null>(null);
  const create = useMutation({
    mutationFn: (captured: Captured & { onCreated: typeof onCreated }) => rpc<ProjectSummary>("project/create", captured.input, captured.key),
    onSuccess: async (response, captured) => {
      pending.current = null;
      await client.invalidateQueries({ queryKey: ["projects"] });
      captured.onCreated(response.value.project_id);
    },
    onSettled: () => {
      if (pending.current) pending.current.inFlight = false;
    },
  });
  const submit = () => {
    if (!name.trim() || pending.current?.inFlight) return;
    const signature = JSON.stringify([name, cutoff, overlay]);
    if (pending.current?.signature !== signature) pending.current = { ...captureProjectCreation(name, cutoff, overlay), signature, inFlight: false };
    pending.current.inFlight = true;
    create.mutate({ ...pending.current, onCreated });
  };
  return (
    <div className="start-page project-create">
      <h1>{t("workspace.newProject")}</h1>
      <p className="muted">{t("workspace.createNotice")}</p>
      <form className="onboarding-card" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label>
          {t("workspace.projectName")}
          <InputGroup required value={name} onChange={event => setName(event.target.value)} placeholder={t("workspace.namePlaceholder")} />
        </label>
        <div className="project-advanced">
          <Button minimal small icon={advancedOpen ? "chevron-up" : "chevron-down"} aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(value => !value)}>
            {t("workspace.advanced")}
          </Button>
          <Collapse isOpen={advancedOpen}>
            <div className="form-row">
              <label>
                {t("workspace.cutoff")}
                <InputGroup required type="datetime-local" value={cutoff} onChange={event => setCutoff(event.target.value)} />
              </label>
              <label>
                {t("workspace.profile")}
                <InputGroup required value={overlay} onChange={event => setOverlay(event.target.value)} />
              </label>
            </div>
          </Collapse>
        </div>
        <Button type="submit" icon="plus" intent="primary" disabled={!name.trim() || !cutoff || !overlay.trim()} loading={create.isPending}>
          {t("workspace.create")}
        </Button>
        {create.error && <Callout intent="danger" role="alert">{create.error.message}</Callout>}
      </form>
    </div>
  );
}

