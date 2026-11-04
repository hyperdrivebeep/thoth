import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, FileInput, HTMLSelect } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { stageFile, type StagedFile, type UploadProgress } from "../api/files";
import { UiError } from "../i18n/UiError";
import { rpc } from "../api/rpcClient";
import { eyebrowLabel } from "./statusLabels";
import { authorityOptions, sourceConnectSchema } from "../api/sourcePolicy";
import { parserFailureText } from "../api/parserError";

/** How the last connect attempt ended. It lives in the query cache, not in this component: the
 *  workspace swaps the whole page out while it reads the project list again after a connect (on
 *  success and on failure), and a message kept in component state went away with the page. */
type ConnectNotice = { kind: "error"; error: Error } | { kind: "success" } | null;
type CapturedFiles = { files: File[]; authority: string; visibility: string; version: number; projectId: string };

export function FileConnectionPanel({ projectId, hosted = false }: { projectId: string; hosted?: boolean }) {
  useTranslation();
  const client = useQueryClient();
  const notice = useQuery<ConnectNotice>({ queryKey: ["source-connect-notice", projectId], queryFn: () => null, enabled: false, staleTime: Infinity, gcTime: Infinity }).data ?? null;
  const setNotice = (value: ConnectNotice, forProject = projectId) => client.setQueryData<ConnectNotice>(["source-connect-notice", forProject], value);
  const [files, setFiles] = useState<File[]>([]);
  const selectionVersion = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const [authority, setAuthority] = useState("UNCLASSIFIED");
  const [visibility, setVisibility] = useState("");
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
    setNotice({ kind: "error", error }, captured.projectId);
    // An earlier file of the batch may have been connected before a later one failed.
    void client.invalidateQueries({queryKey:["projects"]});
    if (ownsSelection(captured)) { setUploadProgress(null); setConnectingName(""); }
  } });
  const progress = uploadProgress ? uploadProgress.value.phase === "processing"
    ? message("fileUploadWaiting", uploadProgress.name)
    : uploadProgress.value.total ? message("fileUploadProgress", uploadProgress.name, Math.min(100, Math.floor(100 * uploadProgress.value.loaded / uploadProgress.value.total)))
    : message("fileStaging", uploadProgress.name)
    : connectingName ? message("fileStaging", connectingName) : "";
  return <section className="source-connect-panel">
    <div className="section-title-row"><div><p className="eyebrow">{eyebrowLabel("AUTHORIZED SOURCES")}</p><h2>{t("sources.fileTitle")}</h2></div><span className="muted">{t("sources.preserve")}</span></div>
    <p className="muted">{t("sources.fileTypes")}{hosted?t("sources.hostedUpload"):t("sources.localUpload")}</p>
    <div className="source-controls"><FileInput fill text={files.length ? message("filesSelected", files.length) : t("sources.chooseFiles")} inputProps={{ref:fileInput,multiple:true,get "aria-label"() { return t("sources.fileLabel"); }}} onInputChange={event => { selectionVersion.current += 1; setNotice(null); setUploadProgress(null); setConnectingName(""); setFiles(Array.from((event.target as HTMLInputElement).files ?? [])); }} />
      <label className="source-control-label"><span>{t("sources.authority")}</span><HTMLSelect aria-label={t("sources.authority")} value={authority} onChange={event=>setAuthority(event.target.value)} options={authorityOptions} /></label>
      <label className="source-control-label"><span>{t("sources.scope")}</span><HTMLSelect aria-label={t("sources.scopeLabel")} value={visibility} onChange={event=>setVisibility(event.target.value)} options={[{value:"",get label() { return t("sources.chooseScope"); }},{value:"PROJECT_SHARED",get label() { return t("sources.projectShared"); }},{value:"EXPLICIT_GRANT",get label() { return t("sources.explicitGrants"); }}]} /></label>
      <Button icon="upload" intent="primary" disabled={!files.length || !visibility || connect.isPending} loading={connect.isPending} onClick={()=>connect.mutate({files,authority,visibility,version:selectionVersion.current,projectId})}>{t("composer.attach")}</Button>
      {canCancel && <Button onClick={() => upload.current?.abort()}>{t("sources.fileUploadCancel")}</Button>}</div>
    {files.length > 0 && <p className="muted">{files.map(file=>file.name).join(" · ")}</p>}{progress && <p role="status">{progress}</p>}
    {notice?.kind === "error" && <Callout compact intent="danger" title={t("sources.connectFailed")}>{connectFailure(notice.error)} {t("sources.checkConnected")}</Callout>}
    {notice?.kind === "success" && <Callout compact intent="success">{t("sources.connected")}</Callout>}
  </section>;
}

/** A refused file reads as a sentence that says what to do; the raw message and code stay in a fold. */
function connectFailure(error: Error) {
  const parser = parserFailureText(error);
  if (!parser) return error.message;
  return <>{parser.text}<details className="technical-details"><summary>{t("sources.technicalDetails")}</summary>{error.message} · {parser.code}</details></>;
}
