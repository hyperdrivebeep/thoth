import { t } from "../i18n/locale";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";

export function SourceTimeConfirmationDialog({open, fileLabel, cutoffAt, onConfirm, onAfter, onSkip}:{open:boolean; fileLabel:string; cutoffAt:string; onConfirm:()=>void; onAfter:()=>void; onSkip:()=>void;}) {
  return <Dialog isOpen={open} onClose={onSkip} title={t("sourceTime.title")} aria-label={t("sourceTime.title")}>
    <DialogBody>
      <Callout intent="warning">{t("sourceTime.automaticFailed")}</Callout>
      <p>{fileLabel}</p>
      <p>{t("labels.cutoff")}{" "}{cutoffAt}</p>
      <p>{t("sourceTime.question")}</p>
    </DialogBody>
    <DialogFooter actions={<>
      <Button intent="primary" onClick={onConfirm}>{t("sourceTime.existed")}</Button>
      <Button onClick={onAfter}>{t("sourceTime.later")}</Button>
      <Button minimal onClick={onSkip}>{t("sourceTime.skip")}</Button>
    </>} />
  </Dialog>;
}
