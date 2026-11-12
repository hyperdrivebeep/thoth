import { Button, Callout, Classes, Dialog, type Intent } from "@blueprintjs/core";
import type { ReactNode } from "react";
import { t, useTranslation } from "../i18n/locale";
import { useReturnFocus } from "./returnFocus";

/**
 * The one confirmation window of the app (archive, stop, disconnect): what will happen, in the screen
 * language, with the action named on its button. A browser confirm box says "OK" and cannot be styled
 * or translated.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  intent = "primary",
  icon,
  pending = false,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  intent?: Intent;
  icon?: "box" | "stop" | "unlink";
  pending?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useTranslation();
  useReturnFocus();
  const close = () => {
    if (!pending) onClose();
  };
  return (
    <Dialog isOpen onClose={close} title={title} className="confirm-dialog">
      <div className={Classes.DIALOG_BODY}>
        {children}
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
          <Button intent={intent} icon={icon} loading={pending} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
