import { Button, Callout, Classes, Dialog, InputGroup } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  permanentDeleteFailure,
  projectAlreadyGone,
  type PermanentDeleteResult,
} from "../api/projectDeletion";
import { rpc } from "../api/rpcClient";
import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import type { ProjectSummary } from "../types";
import { useReturnFocus } from "./returnFocus";

/**
 * Confirms and sends project/permanentDelete for one archived project. The name must be typed
 * exactly. When the server deleted the project but could not remove every file, the dialog stays
 * open and "try again" sends the same request, which finishes the removal.
 */
export function ProjectPermanentDelete({
  target,
  onClose,
  onDeleted,
}: {
  target: ProjectSummary;
  onClose: () => void;
  onDeleted: (projectId: string) => void;
}) {
  useTranslation();
  useReturnFocus();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [leftover, setLeftover] = useState(false);
  const remove = useMutation({
    mutationFn: () =>
      rpc<PermanentDeleteResult>(
        "project/permanentDelete",
        { project_id: target.project_id, confirm_name: name },
        crypto.randomUUID(),
      ),
    onSuccess: (response) => {
      onDeleted(target.project_id);
      if (response.value.cleanup_pending.length > 0) setLeftover(true);
      else onClose();
    },
    onError: (error) => {
      if (projectAlreadyGone(error)) void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const matches = name === target.name;
  const close = () => {
    if (!remove.isPending) onClose();
  };
  return (
    <Dialog
      isOpen
      onClose={close}
      title={message("deleteProjectTitle", target.name)}
      className="project-delete-dialog"
    >
      <div className={Classes.DIALOG_BODY}>
        <p>
          <strong>{t("archive.deleteWarning")}</strong>
        </p>
        <p>{t("archive.deleteRemoves")}</p>
        <p>{t("archive.deleteKeeps")}</p>
        {leftover ? (
          <Callout compact intent="warning" role="alert">
            {t("archive.deleteLeftover")}
          </Callout>
        ) : (
          <label>
            {t("archive.deleteTypeName")}
            <InputGroup
              // eslint-disable-next-line jsx-a11y/no-autofocus -- the person has just opened this window to type the name; its only field takes the focus
              autoFocus
              aria-label={t("archive.deleteNameLabel")}
              placeholder={target.name}
              value={name}
              disabled={remove.isPending}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
        )}
        {remove.error && (
          <Callout compact intent="danger" role="alert">
            {permanentDeleteFailure(remove.error)}
          </Callout>
        )}
        {remove.isPending && (
          <p className="empty-copy" role="status">
            {t("archive.deleting")}
          </p>
        )}
      </div>
      <div className={Classes.DIALOG_FOOTER}>
        <div className={Classes.DIALOG_FOOTER_ACTIONS}>
          <Button disabled={remove.isPending} onClick={close}>
            {leftover ? t("archive.deleteClose") : t("archive.deleteCancel")}
          </Button>
          <Button
            intent="danger"
            icon="trash"
            disabled={!matches || remove.isPending}
            onClick={() => remove.mutate()}
          >
            {leftover ? t("archive.deleteRetry") : t("archive.delete")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
