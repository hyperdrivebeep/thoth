import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, FileInput } from "@blueprintjs/core";
import { Select } from "./Select";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { stageFile, type StagedFile, type UploadProgress } from "../api/files";
import { UiError } from "../i18n/UiError";
import { sentenceGap } from "../i18n/sentences";
import { rpc } from "../api/rpcClient";
import { authorityOptions, sourceConnectSchema } from "../api/sourcePolicy";
import { parserFailureText } from "../api/parserError";
import { refusalBeforeUpload, UPLOAD_ACCEPT, UploadRefused } from "../api/uploadTypes";
import { Disclosure } from "./Disclosure";

/** How the last connect attempt ended. It lives in the query cache, not in this component: the
 *  workspace swaps the whole page out while it reads the project list again after a connect (on
 *  success and on failure), and a message kept in component state went away with the page. */
type ConnectNotice = { kind: "error"; error: Error; connected: number; total: number } | { kind: "success" } | null;
type CapturedFiles = { files: File[]; authority: string; visibility: string; version: number; projectId: string; connected: number };
type Visibility = "" | "PROJECT_SHARED" | "EXPLICIT_GRANT";
const scopeLabel = (value: Visibility) =>
  value === "PROJECT_SHARED" ? t("sources.projectShared") : value === "EXPLICIT_GRANT" ? t("sources.explicitGrants") : t("sources.chooseScope");

export function FileConnectionPanel({ projectId, hosted = false }: { projectId: string; hosted?: boolean }) {
  useTranslation();
  const client = useQueryClient();
  const notice = useQuery<ConnectNotice>({ queryKey: ["source-connect-notice", projectId], queryFn: () => null, enabled: false, staleTime: Infinity, gcTime: Infinity }).data ?? null;
  const setNotice = (value: ConnectNotice, forProject = projectId) => client.setQueryData<ConnectNotice>(["source-connect-notice", forProject], value);
  const [files, setFiles] = useState<File[]>([]);
  const selectionVersion = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const [authority, setAuthority] = useState("UNCLASSIFIED");
  // On one person's own computer a new source is shared within its project unless that is changed under
  // the fold. The hosted review environment still asks, as before.
  const [visibility, setVisibility] = useState<Visibility>(hosted ? "" : "PROJECT_SHARED");
  const [uploadProgress, setUploadProgress] = useState<{name: string; value: UploadProgress} | null>(null);
  const [connectingName, setConnectingName] = useState("");
  const [canCancel, setCanCancel] = useState(false);
  const upload = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; upload.current?.abort(); };
  }, []);
  const ownsSelection = (captured: CapturedFiles) => mounted.current && captured.version === selectionVersion.current && captured.projectId === currentProject.current;
  const connect = useMutation({ mutationFn: async (captured: CapturedFiles) => {
    setNotice(null, captured.projectId);
    // A file the server would refuse is refused here, before anything is sent.
    for (const file of captured.files) {
      const refusal = refusalBeforeUpload(file);
      if (refusal) throw refusal;
    }
    const controller = new AbortController();
    upload.current = controller;
    setCanCancel(true);
    const stagedFiles: StagedFile[] = [];
    try {
      for (const file of captured.files) {
        if (controller.signal.aborted) throw new UiError("sources.fileUploadCancelled");
        if (ownsSelection(captured)) setUploadProgress({ name: file.name, value: {phase: "uploading", loaded: 0, total: file.size} });
        const staged = await stageFile(file, captured.projectId, {
          signal: controller.signal,
          onProgress: value => { if (ownsSelection(captured)) setUploadProgress({ name: file.name, value }); },
        });
        if (controller.signal.aborted) throw new UiError("sources.fileUploadCancelled");
        stagedFiles.push(staged);
      }
      upload.current = null;
      if (mounted.current) setCanCancel(false);
      if (ownsSelection(captured)) setUploadProgress(null);
      for (const staged of stagedFiles) {
        if (ownsSelection(captured)) setConnectingName(staged.filename);
        await rpc("project/source/connect", sourceConnectSchema.parse({project_id:captured.projectId, relative_path:staged.relative_path, media_type:staged.media_type,
          authority:captured.authority, cutoff_state:"ELIGIBLE", security_class:"INTERNAL",
          resource_scope:{owner_kind:"PROJECT",visibility:captured.visibility}, version_label:`web-${staged.byte_sha256.slice(0,12)}`}), crypto.randomUUID());
        captured.connected += 1;
      }
    } finally {
      if (upload.current === controller) upload.current = null;
      if (mounted.current) setCanCancel(false);
    }
  }, onSuccess: async (_result, captured) => {
    setNotice({ kind: "success" }, captured.projectId);
    if (ownsSelection(captured)) {
      setFiles([]); setUploadProgress(null); setConnectingName("");
      if(fileInput.current)fileInput.current.value="";
    }
    await Promise.all([client.invalidateQueries({queryKey:["sources",captured.projectId]}),client.invalidateQueries({queryKey:["evidence",captured.projectId]}),client.invalidateQueries({queryKey:["research",captured.projectId]}),
      // Connecting a source raises the project revision; the sidebar list must not keep the old one.
      client.invalidateQueries({queryKey:["projects"]})]);
  }, onError: (error, captured) => {
    setNotice({ kind: "error", error, connected: captured.connected, total: captured.files.length }, captured.projectId);
    // An earlier file of the batch may have been connected before a later one failed.
    void client.invalidateQueries({queryKey:["projects"]});
    if (ownsSelection(captured)) { setUploadProgress(null); setConnectingName(""); }
  } });
  const progress = progressText(uploadProgress, connectingName);
  return <section className="source-connect-panel">
    <div className="section-title-row"><h2>{t("sources.fileTitle")}</h2></div>
    <p className="muted">{t("sources.fileTypes")}{hosted?t("sources.hostedUpload"):t("sources.localUpload")}</p>
    <div className="source-controls"><FileInput fill buttonText={t("sources.browse")} text={files.length ? message("filesSelected", files.length) : t("sources.chooseFiles")} inputProps={{ref:fileInput,multiple:true,accept:UPLOAD_ACCEPT,get "aria-label"() { return t("sources.fileLabel"); }}} onInputChange={event => { selectionVersion.current += 1; setNotice(null); setUploadProgress(null); setConnectingName(""); setFiles(Array.from((event.target as HTMLInputElement).files ?? [])); }} />
      <label className="source-control-label"><span>{t("sources.authority")}</span><Select aria-label={t("sources.authority")} value={authority} onChange={event=>setAuthority(event.target.value)} options={authorityOptions} /></label>
      {hosted && <ScopeSelect value={visibility} onChange={setVisibility} askFirst />}
      <Button icon="upload" intent="primary" disabled={!files.length || !visibility || connect.isPending} loading={connect.isPending} onClick={()=>connect.mutate({files,authority,visibility,version:selectionVersion.current,projectId,connected:0})}>{t("composer.attach")}</Button>
      {canCancel && <Button onClick={() => upload.current?.abort()}>{t("sources.fileUploadCancel")}</Button>}</div>
    {!hosted && (
      <Disclosure className="source-scope-advanced" label={message("scopeNow", scopeLabel(visibility))}>
        <ScopeSelect value={visibility} onChange={setVisibility} />
        <p className="muted">{t("sources.scopeHelp")}</p>
      </Disclosure>
    )}
    {files.length > 0 && <p className="muted">{files.map(file=>file.name).join(" · ")}</p>}{progress && <p role="status">{progress}</p>}
    {notice?.kind === "error" && (
      <Callout compact intent="danger" title={t("sources.connectFailed")} role="alert">
        {connectFailure(notice.error)}
        {batchOutcome(notice.connected, notice.total)}
      </Callout>
    )}
    {notice?.kind === "success" && <Callout compact intent="success" role="status">{t("sources.connected")}</Callout>}
  </section>;
}

/** What is happening to the file right now, in one sentence. */
function progressText(upload: { name: string; value: UploadProgress } | null, connectingName: string): string {
  if (!upload) return connectingName ? message("fileStaging", connectingName) : "";
  if (upload.value.phase === "processing") return message("fileUploadWaiting", upload.name);
  if (!upload.value.total) return message("fileStaging", upload.name);
  return message("fileUploadProgress", upload.name, Math.min(100, Math.floor(100 * upload.value.loaded / upload.value.total)));
}

/** Who may use a new source. The hosted review environment starts without a choice and asks for one. */
function ScopeSelect({ value, onChange, askFirst = false }: { value: Visibility; onChange: (value: Visibility) => void; askFirst?: boolean }) {
  const options = [
    ...(askFirst ? [{ value: "", get label() { return t("sources.chooseScope"); } }] : []),
    { value: "PROJECT_SHARED", get label() { return t("sources.projectShared"); } },
    { value: "EXPLICIT_GRANT", get label() { return t("sources.explicitGrants"); } },
  ];
  return (
    <label className="source-control-label">
      <span>{t("sources.scope")}</span>
      <Select aria-label={t("sources.scopeLabel")} value={value} onChange={event => onChange(event.target.value as Visibility)} options={options} />
    </label>
  );
}

/** What became of the rest of the selection. One refused file needs no such sentence. */
function batchOutcome(connected: number, total: number) {
  if (total <= 1) return null;
  return sentenceGap() + (connected > 0 ? message("batchSomeConnected", connected, total) : message("batchNoneConnected", total));
}

/** A refused file reads as a sentence that says what to do; the raw message and code stay in a fold. */
function connectFailure(error: Error) {
  // The file's name, the reason and what can be uploaded, in the screen language.
  if (error instanceof UploadRefused) return error.message;
  const parser = parserFailureText(error);
  if (!parser) return error.message;
  return <>{parser.text}<details className="technical-details"><summary>{t("sources.technicalDetails")}</summary>{error.message} · {parser.code}</details></>;
}
