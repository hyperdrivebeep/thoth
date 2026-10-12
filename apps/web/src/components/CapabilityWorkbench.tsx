import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Checkbox, HTMLSelect, InputGroup, Tag, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import { capabilityCatalog, filterCapabilities, namespaceLabels, namespaces, parseInvocation, transportFor, type Capability } from "../api/capabilities";
import { rpc, RpcError } from "../api/rpcClient";
import { RecordInspector } from "./RecordInspector";

function MethodDetail({ method, projectId, threadId }: { method: Capability; projectId: string; threadId?: string }) {
  const client = useQueryClient();
  const [input, setInput] = useState(() => JSON.stringify({ project_id: projectId,
    ...(threadId && method.namespace === "thread" ? { thread_id: threadId } : {}),
  }, null, 2));
  const [confirmed, setConfirmed] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  let validation = "";
  try { parseInvocation(input, projectId); } catch (error) { validation = error instanceof Error ? error.message : t("advanced.invalidInput"); }
  const directRead = transportFor(method.name) === "/rpc/query";
  const command = method.surface !== "QUERY" && !directRead;
  const invocation = useMutation({
    mutationFn: (captured: { input: Record<string, unknown>; key: string }) => rpc<Record<string, unknown>>(method.name, captured.input, captured.key),
    onSuccess: () => { if (command) void client.invalidateQueries(); },
  });
  return <section className="method-detail" aria-label={t("advanced.details")}>
    <div className="section-title-row"><Tag intent={command ? "warning" : "primary"}>{method.surface}</Tag><Tag minimal>{method.canonical ? "canonical" : "compatibility alias"}</Tag></div>
    <h2>{method.name}</h2>
    <dl className="method-meta"><div><dt>{t("advanced.owner")}</dt><dd>{method.canonical_owner}</dd></div><div><dt>{t("advanced.policy")}</dt><dd>{method.policy}</dd></div>
      <div><dt>{t("advanced.entrypoint")}</dt><dd>{method.normal_entrypoint}</dd></div><div><dt>{t("advanced.contract")}</dt><dd>{method.behavioral_evidence.join(" · ")}</dd></div>
      {method.alias_target && <div><dt>{t("advanced.alias")}</dt><dd>{method.alias_target}</dd></div>}
      <div><dt>{t("advanced.transport")}</dt><dd>{transportFor(method.name)}</dd></div></dl>
    <Callout compact intent={command ? "warning" : "primary"} title={command ? t("advanced.command") : directRead ? t("advanced.query") : t("advanced.journaledQuery")}>
      {command ? t("advanced.commandNotice") :
        directRead ? t("advanced.queryNotice") :
        t("advanced.journalNotice")}
    </Callout>
    <label className="editor-label" htmlFor="rpc-input">{t("advanced.input")} <small>{t("advanced.inputHint")}</small></label>
    <TextArea id="rpc-input" className="json-editor" fill rows={9} spellCheck={false} value={input} onChange={event => {
      setInput(event.target.value); setConfirmed(false); setRequestKey(crypto.randomUUID()); invocation.reset();
    }} />
    <p className="muted">{t("advanced.coverageNotice")}</p>
    {validation && <Callout compact intent="warning">{validation}</Callout>}
    {command && <Checkbox checked={confirmed} label={t("advanced.confirm")} onChange={event => setConfirmed(event.currentTarget.checked)} />}
    <div className="method-actions"><Button icon={command ? "play" : "search"} intent={command ? "warning" : "primary"} loading={invocation.isPending}
      disabled={Boolean(validation) || (command && !confirmed)} onClick={() => invocation.mutate({ input: parseInvocation(input, projectId), key: requestKey })}>
      {command ? t("advanced.runCommand") : t("advanced.runQuery")}</Button>
      <Button minimal disabled={invocation.isPending} onClick={() => { setRequestKey(crypto.randomUUID()); setConfirmed(false); invocation.reset(); }}>{t("advanced.newKey")}</Button></div>
    <small className="request-key">{t("labels.retryKey")}{" "}{requestKey}{" "}{t("advanced.sameKey")}</small>
    {invocation.error && <Callout intent="danger" title={t("advanced.failed")}><p>{invocation.error.message}</p>{invocation.error instanceof RpcError && <RecordInspector value={invocation.error.details} />}</Callout>}
    {invocation.data && <div className="rpc-result"><div className="section-title-row"><h3>{t("advanced.response")}</h3><Tag>{invocation.data.state}</Tag></div>
      {invocation.data.operation_id && <p className="muted">operation: {invocation.data.operation_id}{" "}{t("advanced.notCompletion")}</p>}
      <RecordInspector value={invocation.data.value} /></div>}
  </section>;
}

export function CapabilityWorkbench({ projectId, threadId, initialNamespace = "all" }: { projectId: string; threadId?: string; initialNamespace?: string }) {
  const [search, setSearch] = useState("");
  const [namespace, setNamespace] = useState(initialNamespace);
  const [surface, setSurface] = useState("all");
  const [selected, setSelected] = useState("");
  const deferredSearch = useDeferredValue(search);
  const visibleNamespaces = namespaces.filter(value => value !== "projectpack" && value !== "field");
  const methods = filterCapabilities(deferredSearch, namespace, surface).filter(
    method => method.namespace !== "projectpack" && method.namespace !== "field",
  );
  const method = methods.find(item => item.name === selected) ?? methods[0];
  return <section className="capability-workbench">
    <header className="workspace-heading"><div><p className="eyebrow">CAPABILITY WORKBENCH</p><h1>{t("advanced.title")}</h1><p>{t("advanced.intro")}</p></div>
      <Tag minimal>{capabilityCatalog.canonical_method_count} canonical + {capabilityCatalog.compatibility_alias_count} aliases</Tag></header>
    <div className="catalog-toolbar"><InputGroup leftIcon="search" aria-label={t("advanced.search")} placeholder={t("advanced.searchPlaceholder")} value={search} onChange={event => setSearch(event.target.value)} />
      <HTMLSelect aria-label={t("advanced.namespace")} value={namespace} onChange={event => setNamespace(event.target.value)} options={[{value:"all", label:formatMessage("namespaceCount", visibleNamespaces.length)}, ...visibleNamespaces.map(value => ({value, label:`${value} · ${namespaceLabels[value] ?? value}`}))]} />
      <HTMLSelect aria-label={t("advanced.kind")} value={surface} onChange={event => setSurface(event.target.value)} options={[{value:"all",get label() { return t("advanced.allKinds"); }},{value:"QUERY",get label() { return t("advanced.queryKind"); }},{value:"COMMAND",get label() { return t("advanced.commandKind"); }},{value:"QUERY_OR_CONTROL",get label() { return t("advanced.compatibility"); }}]} /></div>
    <div className="catalog-layout"><div className="method-list" aria-label={t("advanced.list")}><div className="catalog-count">{methods.length} visible methods</div>
      {methods.length === 0 && <p className="empty-copy">{t("advanced.noMatch")}</p>}
      {methods.map(item => <Button alignText="left" minimal active={method?.name === item.name} key={item.name} onClick={() => setSelected(item.name)} className="method-item">
        <span className={`method-kind ${item.surface.toLowerCase()}`}>{item.surface === "QUERY" ? "Q" : "C"}</span><span><strong>{item.name}</strong><small>{namespaceLabels[item.namespace] ?? item.namespace}{!item.canonical ? " · alias" : ""}</small></span>
      </Button>)}</div>{method && <MethodDetail key={`${projectId}:${threadId ?? ""}:${method.name}`} method={method} projectId={projectId} threadId={threadId} />}</div>
  </section>;
}
