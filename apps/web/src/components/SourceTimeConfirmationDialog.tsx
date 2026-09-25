import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";

export function SourceTimeConfirmationDialog({open, fileLabel, cutoffAt, onConfirm, onAfter, onSkip}:{open:boolean; fileLabel:string; cutoffAt:string; onConfirm:()=>void; onAfter:()=>void; onSkip:()=>void;}) {
  return <Dialog isOpen={open} onClose={onSkip} title="자료 시점 확인" aria-label="자료 시점 확인">
    <DialogBody>
      <Callout intent="warning">이 자료의 시점을 자동 확인하지 못했습니다.</Callout>
      <p>{fileLabel}</p>
      <p>판단 기준시점: {cutoffAt}</p>
      <p>이 판은, 표시된 판단 기준시점까지 존재하던 자료입니까?</p>
    </DialogBody>
    <DialogFooter actions={<>
      <Button intent="primary" onClick={onConfirm}>기준시점까지 존재</Button>
      <Button onClick={onAfter}>기준시점보다 후</Button>
      <Button minimal onClick={onSkip}>모르겠음 / 이번에는 쓰지 않음</Button>
    </>} />
  </Dialog>;
}
