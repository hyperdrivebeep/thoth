import { Button, Callout, Checkbox, InputGroup, ProgressBar, Switch, Tag } from "@blueprintjs/core";
import { Sentences } from "./Sentences";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelLocalOperation,
  configureRetrievalExperiment,
  readLocalAsset,
  readRetrievalExperiment,
  runLocalOperation,
  WORKSPACE_PROJECT_ID,
  type LocalAssetRead,
  type LocalRetrievalLongMethod,
  type OperationOutcome,
  type OperationTimeouts,
  type RetrievalExperimentRead,
  type RetrievalJob,
} from "../api/localRetrieval";
import { LOCAL_RETRIEVAL_INSTALL_COMMAND } from "../api/localRetrievalInstall";
import { t, useLocale } from "../i18n/locale";
import {
  localRetrievalBytes,
  localRetrievalCounts,
  localRetrievalReason,
  localRetrievalTransfer,
  type LocalRetrievalMessageKey,
} from "../i18n/localRetrievalMessages";
import { useDraftGeneration, type DraftTicket } from "./useDraftGeneration";

type Action = "import" | "install" | "prepare" | "retry";
type CancelState = "NONE" | "REQUESTED" | "REJECTED" | "UNCONFIRMED";
type Operation =
  | { kind: "idle" }
  | { kind: "running"; action: Action; operationId: string | null; cancel: CancelState }
  | { kind: "done"; action: Action; outcome: OperationOutcome };
/** The last request of the card, kept so "check result" repeats it and "run as new" does not. */
type Attempt = {
  action: Action;
  method: LocalRetrievalLongMethod;
  input: Record<string, unknown> & { project_id: string };
  idempotencyKey: string;
  operationId: string | null;
};

const ASSET_ACTIONS: ReadonlySet<Action> = new Set<Action>(["import", "install"]);
const ACTIVE_INDEX_STATES: ReadonlySet<string> = new Set(["INDEXING", "CANCEL_REQUESTED"]);
const IDLE: Operation = { kind: "idle" };

/** The reason code of a shared `RpcError` (its `details.reason_code`); any other error has none. */
function errorReasonCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("details" in error)) return null;
  const details = (error as { details: unknown }).details;
  if (typeof details !== "object" || details === null || !("reason_code" in details)) return null;
  const code = (details as { reason_code: unknown }).reason_code;
  return typeof code === "string" ? code : null;
}

/** State that reads as `initial` whenever the screen scope differs from the one that wrote it. */
function useScopedState<T>(
  scope: string,
  initial: T,
): [T, (update: T | ((previous: T) => T)) => void] {
  const [held, setHeld] = useState<{ scope: string; value: T }>({ scope, value: initial });
  const value = held.scope === scope ? held.value : initial;
  const set = useCallback(
    (update: T | ((previous: T) => T)) =>
      setHeld((previous) => {
        const base = previous.scope === scope ? previous.value : initial;
        return {
          scope,
          value: typeof update === "function" ? (update as (previous: T) => T)(base) : update,
        };
      }),
    [scope, initial],
  );
  return [value, set];
}

/**
 * "Local retrieval preparation": fixed model files, optional runtime component and one project's
 * derived index. Once the parts and the model are installed every project is indexed in the
 * background; this card shows that, switches a project off and starts or repeats the work by hand.
 * In a hosted (review) workspace it only explains that, with no requests.
 */
export function LocalRetrievalSettingsCard({
  workspaceId,
  projectId,
  hosted = false,
  timeouts,
  part = "all",
}: {
  workspaceId: string;
  projectId?: string;
  hosted?: boolean;
  /** "computer": the model files and the runtime part, which every project shares. "project": this project's index. */
  part?: "all" | "computer" | "project";
  /** Test seam only: shorter waits for the long-operation wrapper. */
  timeouts?: Partial<OperationTimeouts>;
}) {
  const locale = useLocale();
  const text = t;
  const client = useQueryClient();
  const mode = hosted ? "hosted" : "local";
  /** The model files belong to a workspace; the index and the work shown here to a workspace/project screen. */
  const assetScope = `${workspaceId}:${mode}`;
  const scope = `${assetScope}:${projectId ?? ""}`;
  const ownership = useDraftGeneration(scope);
  const assetKey = ["local-retrieval-asset", workspaceId];
  const projectKey = ["local-retrieval-project", workspaceId, projectId ?? ""];

  // Work shown here belongs to one screen; another screen starts idle and never inherits it.
  const [operation, setOperation] = useScopedState<Operation>(scope, IDLE);
  const [configureNotice, setConfigureNotice] = useScopedState<"CONFLICT" | "FAILED" | null>(
    scope,
    null,
  );
  const [sourcePath, setSourcePath] = useScopedState(assetScope, "");
  const [confirmedDigest, setConfirmedDigest] = useScopedState<string | null>(assetScope, null);
  const attempt = useRef<Attempt | null>(null);
  const controller = useRef<AbortController | null>(null);

  const running = operation.kind === "running";
  const runningAction: Action | null = operation.kind === "running" ? operation.action : null;
  const assetWorking = runningAction !== null && ASSET_ACTIONS.has(runningAction);
  const indexWorking = runningAction !== null && !ASSET_ACTIONS.has(runningAction);
  const asset = useQuery({
    queryKey: assetKey,
    enabled: !hosted,
    queryFn: ({ signal }) => readLocalAsset(signal),
    refetchInterval: (query) => (query.state.data?.value.active || assetWorking ? 1500 : false),
  });
  const project = useQuery({
    queryKey: projectKey,
    enabled: !hosted && Boolean(projectId),
    queryFn: ({ signal }) => readRetrievalExperiment(projectId ?? "", signal),
    refetchInterval: (query) => {
      const state = query.state.data?.value.state;
      return (state && ACTIVE_INDEX_STATES.has(state)) || indexWorking ? 1500 : false;
    },
  });
  const configure = useMutation({
    mutationFn: ({
      enabled,
      expectedGeneration,
    }: {
      enabled: boolean;
      expectedGeneration: number;
      ticket: DraftTicket;
    }) =>
      configureRetrievalExperiment(
        { project_id: projectId ?? "", enabled, expected_generation: expectedGeneration },
        crypto.randomUUID(),
      ),
    onSuccess: (result, variables) => {
      if (!ownership.isCurrent(variables.ticket)) return;
      setConfigureNotice(null);
      client.setQueryData(projectKey, result);
    },
    onError: async (error, variables) => {
      if (!ownership.isCurrent(variables.ticket)) return;
      setConfigureNotice(
        errorReasonCode(error) === "EMBEDDING_CONFIG_CONFLICT" ? "CONFLICT" : "FAILED",
      );
      await client.invalidateQueries({ queryKey: projectKey });
    },
  });

  // A screen change stops waiting on the previous screen's work and forgets it: its late result is
  // never shown, and a later return to that screen cannot "check" or "re-run" the old request.
  useEffect(() => {
    const holders = { controller, attempt };
    return () => {
      holders.controller.current?.abort();
      holders.controller.current = null;
      holders.attempt.current = null;
      setOperation(IDLE);
      setConfigureNotice(null);
    };
  }, [scope, setOperation, setConfigureNotice]);
  // The download consent and the typed folder belong to one workspace's model files only.
  useEffect(
    () => () => {
      setConfirmedDigest(null);
      setSourcePath("");
    },
    [assetScope, setConfirmedDigest, setSourcePath],
  );

  const start = async (
    action: Action,
    method: LocalRetrievalLongMethod,
    input: Attempt["input"],
    resume?: Pick<Attempt, "idempotencyKey" | "operationId">,
  ) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    ownership.advance();
    const ticket = ownership.capture();
    const current: Attempt = {
      action,
      method,
      input,
      idempotencyKey: resume?.idempotencyKey ?? crypto.randomUUID(),
      operationId: resume?.operationId ?? null,
    };
    attempt.current = current;
    setOperation({ kind: "running", action, operationId: current.operationId, cancel: "NONE" });
    const outcome = await runLocalOperation({
      method,
      input,
      idempotencyKey: current.idempotencyKey,
      operationId: current.operationId,
      signal: abort.signal,
      timeouts,
      onStarted: (operationId) => {
        current.operationId = operationId;
        if (ownership.isCurrent(ticket))
          setOperation((previous) =>
            previous.kind === "running" ? { ...previous, operationId } : previous,
          );
      },
    });
    if (!ownership.isCurrent(ticket)) return;
    if (outcome.kind === "UNCONFIRMED" && outcome.operationId)
      current.operationId = outcome.operationId;
    setOperation({ kind: "done", action, outcome });
    await client.invalidateQueries({ queryKey: assetKey });
    if (projectId) await client.invalidateQueries({ queryKey: projectKey });
  };
  const checkResult = () => {
    const last = attempt.current;
    if (last)
      void start(last.action, last.method, last.input, {
        idempotencyKey: last.idempotencyKey,
        operationId: last.operationId,
      });
  };
  const runAgain = () => {
    const last = attempt.current;
    if (last) void start(last.action, last.method, last.input);
  };
  const requestCancel = async () => {
    const last = attempt.current;
    if (!last?.operationId || operation.kind !== "running") return;
    const ticket = ownership.capture();
    setOperation((previous) =>
      previous.kind === "running" ? { ...previous, cancel: "REQUESTED" } : previous,
    );
    const result = await cancelLocalOperation(
      last.input.project_id,
      last.operationId,
      controller.current?.signal,
      timeouts?.cancelMs,
    );
    if (!ownership.isCurrent(ticket) || result.kind === "REQUESTED") return;
    setOperation((previous) =>
      previous.kind === "running" ? { ...previous, cancel: result.kind } : previous,
    );
  };

  if (hosted) {
    return (
      <section className="detail-card local-retrieval-card" aria-labelledby="local-retrieval-title">
        <h2 id="local-retrieval-title">{text("localRetrieval.title")}</h2>
        <Callout icon="info-sign" role="status">
          {text("localRetrieval.hostedOnly")}
        </Callout>
      </section>
    );
  }

  const assetValue = asset.data?.value;
  const projectValue = project.data?.value;
  const importInput = {
    project_id: WORKSPACE_PROJECT_ID,
    source_path: sourcePath.trim(),
    manifest_digest: assetValue?.manifest_digest ?? "",
  };
  const installConfirmed = Boolean(assetValue) && confirmedDigest === assetValue?.manifest_digest;

  const shown = PARTS[part];
  return (
    <section className="detail-card local-retrieval-card" aria-labelledby="local-retrieval-title">
      <h2 id="local-retrieval-title">{text(shown.title)}</h2>
      {shown.asset && (
        <p className="muted">
          <Sentences text={text("localRetrieval.intro")} />
        </p>
      )}

      <AssetReadStatus read={asset} text={text} />
      {shown.asset && assetValue?.supported && (
        <AssetSection
          asset={assetValue}
          text={text}
          running={running}
          sourcePath={sourcePath}
          onSourcePath={setSourcePath}
          installConfirmed={installConfirmed}
          onConfirmInstall={(checked) =>
            setConfirmedDigest(checked ? assetValue.manifest_digest : null)
          }
          onImport={() => void start("import", "model/localAsset/import", importInput)}
          onInstall={() =>
            void start("install", "model/localAsset/install", {
              project_id: WORKSPACE_PROJECT_ID,
              manifest_digest: assetValue.manifest_digest,
              confirm_download: true,
            })
          }
          reason={(code) => localRetrievalReason(code, locale)}
          transfer={assetValue.active ? localRetrievalTransfer(assetValue.active, locale) : null}
          bytes={localRetrievalBytes(assetValue.total_bytes, locale)}
        />
      )}

      {shown.projectHeading && <h3>{text("localRetrieval.projectTitle")}</h3>}
      {shown.project && (
        <ProjectReadStatus
          hasProject={Boolean(projectId)}
          failed={Boolean(project.error)}
          text={text}
        />
      )}
      {shown.project && projectValue && (
        <ProjectSection
          project={projectValue}
          asset={assetValue ?? null}
          text={text}
          running={running}
          configuring={configure.isPending}
          configureNotice={configureNotice}
          onToggle={(enabled) =>
            configure.mutate({
              enabled,
              expectedGeneration: projectValue.generation,
              ticket: ownership.capture(),
            })
          }
          onPrepare={() =>
            void start("prepare", "project/retrieval/experiment/prepare", {
              project_id: projectValue.project_id,
              expected_generation: projectValue.generation,
            })
          }
          onRetry={(job) =>
            void start("retry", "project/embeddingIndex/retry", {
              project_id: projectValue.project_id,
              job_id: job.job_id,
              generation: job.generation,
            })
          }
          reason={(code) => localRetrievalReason(code, locale)}
          counts={localRetrievalCounts(
            {
              ready: projectValue.ready_sources,
              total: projectValue.total_sources,
              pending: projectValue.pending_sources,
              failed: projectValue.failed_sources,
            },
            locale,
          )}
        />
      )}

      <OperationStatus
        operation={operation}
        text={text}
        reason={(code) => localRetrievalReason(code, locale)}
        onCancel={() => void requestCancel()}
        onCheckResult={checkResult}
        onRunAgain={runAgain}
      />
    </section>
  );
}

type Text = (key: LocalRetrievalMessageKey) => string;

/** What each form of the card shows. "all" is the whole card, as before it could be split over two screens. */
const PARTS = {
  all: { asset: true, project: true, projectHeading: true, title: "localRetrieval.title" },
  computer: { asset: true, project: false, projectHeading: false, title: "localRetrieval.title" },
  project: {
    asset: false,
    project: true,
    projectHeading: false,
    title: "localRetrieval.projectTitle",
  },
} as const;

/** Whether the model files could be read at all: still reading, failed, or not supported on this computer. */
function AssetReadStatus({
  read,
  text,
}: {
  read: { isPending: boolean; error: unknown; data?: { value: LocalAssetRead } };
  text: Text;
}) {
  if (read.isPending)
    return (
      <p className="muted" role="status">
        …
      </p>
    );
  if (read.error)
    return (
      <Callout intent="warning" role="alert">
        {text("localRetrieval.readFailed")}
      </Callout>
    );
  return read.data?.value.supported === false ? (
    <Callout intent="warning" role="status">
      {text("localRetrieval.unsupported")}
    </Callout>
  ) : null;
}

/** The project's index could not be shown: no project is open, or its read failed. */
function ProjectReadStatus({
  hasProject,
  failed,
  text,
}: {
  hasProject: boolean;
  failed: boolean;
  text: Text;
}) {
  if (!hasProject) return <p className="muted">{text("localRetrieval.noProject")}</p>;
  return failed ? (
    <Callout intent="warning" role="alert">
      {text("localRetrieval.readFailed")}
    </Callout>
  ) : null;
}

/**
 * Whether to say that an index made under an earlier preprocessing version is being rebuilt. That
 * is only true when something can run it: the parts and the model are installed, the project is on
 * and no source is waiting for a retry.
 */
function rebuildIsRunning(project: RetrievalExperimentRead, asset: LocalAssetRead | null): boolean {
  return (
    Boolean(project.outdated_index) &&
    project.enabled &&
    project.failed_sources === 0 &&
    asset?.asset_state === "READY" &&
    asset.dependency_available
  );
}
/** Shown only until the sources are indexed again under the current preprocessing version. */
function OutdatedIndexNotice({ outdated, text }: { outdated: boolean | undefined; text: Text }) {
  return outdated ? (
    <Callout compact intent="warning">
      {text("localRetrieval.outdatedIndex")}
    </Callout>
  ) : null;
}
function AssetSection({
  asset,
  text,
  running,
  sourcePath,
  onSourcePath,
  installConfirmed,
  onConfirmInstall,
  onImport,
  onInstall,
  reason,
  transfer,
  bytes,
}: {
  asset: LocalAssetRead;
  text: Text;
  running: boolean;
  sourcePath: string;
  onSourcePath: (value: string) => void;
  installConfirmed: boolean;
  onConfirmInstall: (checked: boolean) => void;
  onImport: () => void;
  onInstall: () => void;
  reason: (code: string | null) => string;
  transfer: string | null;
  bytes: string;
}) {
  const stateIntent =
    asset.asset_state === "READY" ? "success" : asset.asset_state === "FAILED" ? "danger" : "none";
  return (
    <div className="local-retrieval-asset">
      <p>
        <strong>{text("localRetrieval.modelLabel")}</strong>: {asset.model_name} · {bytes} ·{" "}
        <code>{asset.manifest_digest.slice(0, 12)}</code>
      </p>
      <p className="muted">{text("localRetrieval.runtimeNote")}</p>
      <p>
        <Tag minimal round intent={stateIntent}>
          {text(`localRetrieval.asset.${asset.asset_state}`)}
        </Tag>{" "}
        <Tag minimal round intent={asset.dependency_available ? "success" : "warning"}>
          {text(
            asset.dependency_available
              ? "localRetrieval.dependencyAvailable"
              : "localRetrieval.dependencyMissing",
          )}
        </Tag>{" "}
        <Tag minimal round>
          {text(
            asset.worker_loaded ? "localRetrieval.workerLoaded" : "localRetrieval.workerNotLoaded",
          )}
        </Tag>
      </p>
      {!asset.dependency_available && (
        <div className="local-retrieval-runtime-help">
          <p className="muted">{text("localRetrieval.installRuntimeHelp")}</p>
          <code className="local-retrieval-command">{LOCAL_RETRIEVAL_INSTALL_COMMAND}</code>
          <p className="muted">{text("localRetrieval.installRuntimeRestart")}</p>
        </div>
      )}
      {asset.asset_state === "FAILED" && (
        <p className="result-notice" role="status">
          {reason(asset.reason_code)}
        </p>
      )}
      {asset.active && (
        <div role="status" aria-label={text("localRetrieval.operationLabel")}>
          <ProgressBar
            intent="primary"
            aria-label={text("localRetrieval.operationLabel")}
            aria-valuetext={transfer ?? undefined}
            value={
              asset.active.total_bytes > 0
                ? Math.min(1, asset.active.transferred_bytes / asset.active.total_bytes)
                : undefined
            }
          />
          <small className="muted">{transfer}</small>
        </div>
      )}
      {!asset.can_manage && (
        <p className="muted" role="status">
          {text("localRetrieval.manageDenied")}
        </p>
      )}
      {asset.can_manage && asset.asset_state !== "READY" && (
        <>
          <h4>{text("localRetrieval.importTitle")}</h4>
          <p className="muted">{text("localRetrieval.importHelp")}</p>
          <div className="first-run-key">
            <InputGroup
              id="local-retrieval-source-path"
              value={sourcePath}
              aria-label={text("localRetrieval.importPathLabel")}
              placeholder={text("localRetrieval.importPathLabel")}
              onChange={(event) => onSourcePath(event.target.value)}
              disabled={running || Boolean(asset.active)}
            />
            <Button
              onClick={onImport}
              disabled={running || Boolean(asset.active) || !sourcePath.trim()}
            >
              {text("localRetrieval.importButton")}
            </Button>
          </div>
          <h4>{text("localRetrieval.installTitle")}</h4>
          <Callout icon="info-sign">{text("localRetrieval.installNotice")}</Callout>
          <Checkbox
            id="local-retrieval-install-confirm"
            checked={installConfirmed}
            label={text("localRetrieval.installConfirm")}
            disabled={running || Boolean(asset.active)}
            onChange={(event) => onConfirmInstall(event.currentTarget.checked)}
          />
          <Button
            intent="primary"
            onClick={onInstall}
            disabled={running || Boolean(asset.active) || !installConfirmed}
          >
            {text("localRetrieval.installButton")}
          </Button>
        </>
      )}
    </div>
  );
}

function ProjectSection({
  project,
  asset,
  text,
  running,
  configuring,
  configureNotice,
  onToggle,
  onPrepare,
  onRetry,
  reason,
  counts,
}: {
  project: RetrievalExperimentRead;
  asset: LocalAssetRead | null;
  text: Text;
  running: boolean;
  configuring: boolean;
  configureNotice: "CONFLICT" | "FAILED" | null;
  onToggle: (enabled: boolean) => void;
  onPrepare: () => void;
  onRetry: (job: RetrievalJob) => void;
  reason: (code: string | null) => string;
  counts: string;
}) {
  const indexActive =
    ACTIVE_INDEX_STATES.has(project.state) || Boolean(project.active_operation_id);
  // Work started by a person (it has an operation id) holds the controls. The background indexer
  // does not: the server takes "switch off" (it stops the run) and "prepare" (it waits for the job
  // in hand) while it works, so the controls stay usable.
  const explicitActive = Boolean(project.active_operation_id);
  const blocked: LocalRetrievalMessageKey | null = !project.can_prepare
    ? "localRetrieval.prepareNotAllowed"
    : !project.enabled
      ? "localRetrieval.prepareBlockedOff"
      : asset?.asset_state !== "READY"
        ? "localRetrieval.prepareBlockedAsset"
        : !asset.dependency_available
          ? "localRetrieval.prepareBlockedDependency"
          : null;
  const failedJobs = project.jobs.filter((job) => job.state === "FAILED");
  return (
    <div className="local-retrieval-project">
      <Switch
        id="local-retrieval-enable"
        checked={project.enabled}
        label={text("localRetrieval.enableLabel")}
        disabled={!project.can_prepare || configuring || running || explicitActive}
        onChange={(event) => onToggle(event.currentTarget.checked)}
      />
      <small className="muted">{text("localRetrieval.enableHelp")}</small>
      {configureNotice && (
        <p className="result-notice" role="alert">
          {text(
            configureNotice === "CONFLICT"
              ? "localRetrieval.configureConflict"
              : "localRetrieval.configureFailed",
          )}
        </p>
      )}
      <p>
        <Tag
          minimal
          round
          intent={
            project.state === "READY" ? "success" : project.state === "FAILED" ? "danger" : "none"
          }
        >
          {text(`localRetrieval.index.${project.state}`)}
        </Tag>{" "}
        <span>{counts}</span>
      </p>
      {project.total_sources > 0 && (
        <ProgressBar
          intent={project.state === "READY" ? "success" : "primary"}
          animate={indexActive}
          stripes={indexActive}
          aria-label={text(`localRetrieval.index.${project.state}`)}
          aria-valuetext={counts}
          value={Math.min(1, project.ready_sources / project.total_sources)}
        />
      )}
      <OutdatedIndexNotice outdated={rebuildIsRunning(project, asset)} text={text} />
      <p>
        <Button
          intent="primary"
          onClick={onPrepare}
          disabled={Boolean(blocked) || running || explicitActive}
        >
          {text("localRetrieval.prepareButton")}
        </Button>
        {blocked && (
          <small className="muted" role="status">
            {" "}
            {text(blocked)}
          </small>
        )}
      </p>
      <p className="muted">{text("localRetrieval.readyNote")}</p>
      {project.jobs.length > 0 && (
        <details>
          <summary>{text("localRetrieval.jobsTitle")}</summary>
          <ul className="local-retrieval-jobs">
            {project.jobs.map((job) => (
              <li key={job.job_id}>
                <Tag
                  minimal
                  round
                  intent={
                    job.state === "READY" ? "success" : job.state === "FAILED" ? "danger" : "none"
                  }
                >
                  {text(`localRetrieval.job.${job.state}`)}
                </Tag>{" "}
                <code>{job.job_id.slice(0, 12)}</code>
                {job.reason_code && <small className="muted"> · {reason(job.reason_code)}</small>}
                {job.state === "FAILED" && (
                  <Button
                    small
                    minimal
                    icon="repeat"
                    disabled={!project.can_prepare || running || explicitActive || Boolean(blocked)}
                    onClick={() => onRetry(job)}
                  >
                    {text("localRetrieval.retryButton")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {failedJobs.length > 0 && project.state === "FAILED" && (
        <p className="muted">{reason(failedJobs[0].reason_code)}</p>
      )}
    </div>
  );
}

function OperationStatus({
  operation,
  text,
  reason,
  onCancel,
  onCheckResult,
  onRunAgain,
}: {
  operation: Operation;
  text: Text;
  reason: (code: string | null) => string;
  onCancel: () => void;
  onCheckResult: () => void;
  onRunAgain: () => void;
}) {
  if (operation.kind === "idle") return null;
  const label = text(`localRetrieval.op.${operation.action}`);
  if (operation.kind === "running") {
    const cancelText: LocalRetrievalMessageKey | null =
      operation.cancel === "REQUESTED"
        ? "localRetrieval.opCancelRequested"
        : operation.cancel === "REJECTED"
          ? "localRetrieval.opCancelRejected"
          : operation.cancel === "UNCONFIRMED"
            ? "localRetrieval.opCancelUnconfirmed"
            : null;
    return (
      <div className="local-retrieval-operation" role="status" data-operation-state="running">
        <p>
          <strong>{label}</strong>:{" "}
          {text(operation.operationId ? "localRetrieval.opRunning" : "localRetrieval.opStarting")}
        </p>
        {cancelText && <p className="result-notice">{text(cancelText)}</p>}
        {operation.operationId && operation.cancel === "NONE" && (
          <Button small onClick={onCancel}>
            {text("localRetrieval.cancelButton")}
          </Button>
        )}
      </div>
    );
  }
  const { outcome } = operation;
  const line =
    outcome.kind === "SUCCEEDED"
      ? text("localRetrieval.opSucceeded")
      : outcome.kind === "FAILED"
        ? `${text("localRetrieval.opFailed")} · ${reason(outcome.reasonCode)}`
        : outcome.kind === "CANCELLED"
          ? text("localRetrieval.opCancelled")
          : outcome.kind === "REJECTED"
            ? `${text("localRetrieval.opRejected")} · ${reason(outcome.reasonCode)}`
            : outcome.kind === "ABANDONED"
              ? text("localRetrieval.opAbandoned")
              : text(
                  outcome.operationId
                    ? "localRetrieval.opUnconfirmed"
                    : "localRetrieval.opStartUnconfirmed",
                );
  const unconfirmed = outcome.kind === "UNCONFIRMED";
  return (
    <div
      className="local-retrieval-operation"
      role="status"
      data-operation-state={outcome.kind.toLowerCase()}
    >
      <p>
        <strong>{label}</strong>: {line}
      </p>
      {unconfirmed && (
        <p>
          <Button small onClick={onCheckResult}>
            {text("localRetrieval.confirmResultButton")}
          </Button>{" "}
          <Button small minimal onClick={onRunAgain}>
            {text("localRetrieval.newRunButton")}
          </Button>
        </p>
      )}
    </div>
  );
}
