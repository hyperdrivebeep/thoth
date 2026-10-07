import { Button, Callout, Classes, Dialog, Radio, RadioGroup } from "@blueprintjs/core";
import { useState } from "react";
import { applyTraceImport, previewTraceImport, type ImportApplied, type ImportMode, type ImportPreview } from "../api/trace";
import { applyFailureText, issueText, lossText } from "./traceText";

const kindName: Record<string, string> = { ITEM: "항목", LINK: "연결", RULE: "판정 규칙", RESULT: "결과" };

function readText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function ChangeList({ heading, items }: { heading: string; items: { kind: string; id: string; fields: string[] }[] }) {
  if (items.length === 0) return null;
  return <div><strong>{heading} {items.length}개</strong>
    <ul className="trace-reasons">{items.map(item => <li key={item.kind + item.id}>{kindName[item.kind] ?? item.kind} {item.id}</li>)}</ul></div>;
}

/** Choose a file, look at what it would change, then apply exactly that. Nothing is saved before the last button. */
export function TraceImportDialog({ projectId, isOpen, hasTrace, onClose, onApplied }: {
  projectId: string; isOpen: boolean; hasTrace: boolean; onClose: () => void; onApplied: (result: ImportApplied) => void;
}) {
  const [mode, setMode] = useState<ImportMode>(hasTrace ? "UPDATE" : "CREATE");
  const [text, setText] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; next: string } | null>(null);
  const reset = () => { setText(null); setFileName(""); setPreview(null); setFailure(null); setBusy(false); };
  const close = () => { reset(); onClose(); };
  const look = async (nextMode: ImportMode, csv: string | null) => {
    setPreview(null); setFailure(null);
    if (csv === null) return;
    setBusy(true);
    try { setPreview(await previewTraceImport(projectId, nextMode, csv)); }
    catch { setFailure({ message: "파일을 미리보기하지 못했습니다. 아무것도 저장되지 않았습니다.", next: "잠시 뒤 다시 시도하세요." }); }
    finally { setBusy(false); }
  };
  const choose = async (file: File | undefined) => {
    if (!file) return;
    const csv = await readText(file);
    setText(csv); setFileName(file.name);
    await look(mode, csv);
  };
  const apply = async () => {
    if (!preview || text === null) return;
    setBusy(true); setFailure(null);
    try { const result = await applyTraceImport(projectId, mode, text, preview); reset(); onApplied(result); }
    catch (error) { setFailure(applyFailureText(error)); setBusy(false); }
  };
  const changes = preview?.changes;
  const total = changes ? changes.added.length + changes.updated.length + changes.deleted.length : 0;
  return <Dialog isOpen={isOpen} onClose={close} title="CSV 들여오기" className="trace-import-dialog">
    <div className={Classes.DIALOG_BODY}>
      <RadioGroup label="들여오는 방식" selectedValue={mode} onChange={event => { const next = event.currentTarget.value as ImportMode; setMode(next); void look(next, text); }}>
        <Radio value="CREATE" label="새로 만들기 (추적표가 없는 프로젝트)"/>
        <Radio value="UPDATE" label="기존 표 고치기 (내보낸 파일을 고친 것)"/>
      </RadioGroup>
      <label className="trace-file-field">CSV 파일 선택
        <input type="file" accept=".csv,text/csv" aria-label="CSV 파일 선택" onChange={event => void choose(event.currentTarget.files?.[0])}/></label>
      {fileName && <p className="muted">선택한 파일: {fileName}</p>}
      {busy && <p role="status">처리 중…</p>}
      {failure && <Callout intent="danger" role="alert">{failure.message}<p><strong>다음 행동</strong>: {failure.next}</p></Callout>}
      {preview && changes && <section aria-label="들여오기 미리보기" className="trace-preview">
        <h3>미리보기 — 아직 저장하지 않았습니다</h3>
        <p>추가 {changes.added.length}개 · 변경 {changes.updated.length}개 · 삭제 {changes.deleted.length}개 · 그대로 {changes.unchanged}개</p>
        <ChangeList heading="추가" items={changes.added}/><ChangeList heading="변경" items={changes.updated}/><ChangeList heading="삭제" items={changes.deleted}/>
        {preview.conflicts.length > 0 && <Callout intent="danger" title="반영할 수 없는 문제" role="alert">
          <ul className="trace-reasons">{preview.conflicts.map((issue, index) => { const info = issueText(issue);
            return <li key={index}>{info.message}<br/><strong>다음 행동</strong>: {info.next}
              <details className="connection-tech"><summary>기술 정보</summary><small>{issue.code}{issue.detail ? ` · ${issue.detail}` : ""}</small></details></li>; })}</ul></Callout>}
        {preview.losses.length > 0 && <details className="connection-tech"><summary>CSV에 담기지 않아 이 들여오기가 건드리지 않는 것</summary>
          <ul className="trace-reasons">{preview.losses.map(line => <li key={line}>{lossText(line)}</li>)}</ul></details>}
        {preview.applicable && total === 0 && <p>바뀌는 것이 없습니다.</p>}
        {preview.applicable && total > 0 && <p className="muted">반영하면 바뀐 입력에 해당하는 판정이 같은 순간에 다시 계산됩니다. 이전 판정은 이력에 남습니다.</p>}
      </section>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" disabled={!preview || !preview.applicable || total === 0 || busy} onClick={() => void apply()}>이 내용으로 반영</Button>
    </div></div>
  </Dialog>;
}
