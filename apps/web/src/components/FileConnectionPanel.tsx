import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, FileInput, HTMLSelect } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { stageFile } from "../api/files";
import { rpc } from "../api/rpcClient";
import { eyebrowLabel } from "./statusLabels";
import { authorityOptions, sourceConnectSchema } from "../api/sourcePolicy";

export function FileConnectionPanel({ projectId, hosted = false }: { projectId: string; hosted?: boolean }) {
  useTranslation();
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [authority, setAuthority] = useState("UNCLASSIFIED");
  const [visibility, setVisibility] = useState("");
  const [progress, setProgress] = useState("");
  const connect = useMutation({ mutationFn: async (captured: {files: File[]; authority: string; visibility: string}) => {
    for (const file of captured.files) {
      setProgress(message("fileStaging", file.name));
      const staged = await stageFile(file, projectId);
      await rpc("project/source/connect", sourceConnectSchema.parse({project_id:projectId, relative_path:staged.relative_path, media_type:staged.media_type,
        authority:captured.authority, cutoff_state:"ELIGIBLE", security_class:"INTERNAL",
        resource_scope:{owner_kind:"PROJECT",visibility:captured.visibility}, version_label:`web-${staged.byte_sha256.slice(0,12)}`}), crypto.randomUUID());
    }
  }, onSuccess: async () => {
    setFiles([]); setProgress("");
    if(fileInput.current)fileInput.current.value="";
    await Promise.all([client.invalidateQueries({queryKey:["sources",projectId]}),client.invalidateQueries({queryKey:["evidence",projectId]}),client.invalidateQueries({queryKey:["research",projectId]})]);
  }, onError: () => setProgress("") });
  return <section className="source-connect-panel">
    <div className="section-title-row"><div><p className="eyebrow">{eyebrowLabel("AUTHORIZED SOURCES")}</p><h2>{t("sources.fileTitle")}</h2></div><span className="muted">{t("sources.preserve")}</span></div>
    <p className="muted">{t("sources.fileTypes")}{hosted?t("sources.hostedUpload"):t("sources.localUpload")}</p>
    <div className="source-controls"><FileInput fill text={files.length ? message("filesSelected", files.length) : t("sources.chooseFiles")} inputProps={{ref:fileInput,multiple:true,get "aria-label"() { return t("sources.fileLabel"); }}} onInputChange={event => setFiles(Array.from((event.target as HTMLInputElement).files ?? []))} />
      <label className="source-control-label"><span>{t("sources.authority")}</span><HTMLSelect aria-label={t("sources.authority")} value={authority} onChange={event=>setAuthority(event.target.value)} options={authorityOptions} /></label>
      <label className="source-control-label"><span>{t("sources.scope")}</span><HTMLSelect aria-label={t("sources.scopeLabel")} value={visibility} onChange={event=>setVisibility(event.target.value)} options={[{value:"",get label() { return t("sources.chooseScope"); }},{value:"PROJECT_SHARED",get label() { return t("sources.projectShared"); }},{value:"EXPLICIT_GRANT",get label() { return t("sources.explicitGrants"); }}]} /></label>
      <Button icon="upload" intent="primary" disabled={!files.length || !visibility} loading={connect.isPending} onClick={()=>connect.mutate({files,authority,visibility})}>{t("composer.attach")}</Button></div>
    {files.length > 0 && <p className="muted">{files.map(file=>file.name).join(" · ")}</p>}{progress && <p role="status">{progress}</p>}
    {connect.error && <Callout compact intent="danger" title={t("sources.connectFailed")}>{connect.error.message} {t("sources.checkConnected")}</Callout>}
    {connect.isSuccess && <Callout compact intent="success">{t("sources.connected")}</Callout>}
  </section>;
}
