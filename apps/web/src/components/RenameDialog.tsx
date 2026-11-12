import { Button, Callout, Classes, Dialog, InputGroup } from "@blueprintjs/core";
import { useState, type ReactNode } from "react";
import { t, useTranslation } from "../i18n/locale";

/** The longest name the server stores for a project or a conversation. */
const NAME_LIMIT = 500;

/** Asks for a new name. Saving is off while the name is empty or unchanged. */
export function RenameDialog({ title, current, notice, pending = false, error, onSave, onClose }: {
  title: string;
  current: string;
  notice?: ReactNode;
  pending?: boolean;
  error?: string | null;
  onSave: (name: string) => void;
  onClose: () => void;
}) {
  useTranslation();
  const [name, setName] = useState(current);
  const trimmed = name.trim();
  const canSave = trimmed !== "" && trimmed !== current.trim() && !pending;
  const close = () => {
    if (!pending) onClose();
  };
  return (
    <Dialog isOpen onClose={close} title={title} className="rename-dialog">
      <form
        onSubmit={event => {
          event.preventDefault();
          if (canSave) onSave(trimmed);
        }}
      >
        <div className={Classes.DIALOG_BODY}>
          <label>
            {t("manage.nameLabel")}
            <InputGroup
              autoFocus
              aria-label={t("manage.nameLabel")}
              value={name}
              maxLength={NAME_LIMIT}
              disabled={pending}
              onChange={event => setName(event.target.value)}
            />
          </label>
          {notice && <p className="muted">{notice}</p>}
          {error && (
            <Callout compact intent="danger" role="alert">
              {error}
            </Callout>
          )}
        </div>
        <div className={Classes.DIALOG_FOOTER}>
          <div className={Classes.DIALOG_FOOTER_ACTIONS}>
            <Button disabled={pending} onClick={close}>
              {t("manage.cancel")}
            </Button>
            <Button type="submit" intent="primary" disabled={!canSave} loading={pending}>
              {t("manage.save")}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
