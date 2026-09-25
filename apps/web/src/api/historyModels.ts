/** UI models. Wire responses are validated and mapped in history.ts/restore.ts. */
export type HistoryScope = { projectId: string; threadId?: string; requestDigest?: string };
export type HistoryKind = "REQUEST" | "RESULT" | "REVISION" | "RESTORE" | "MEMORY";
export type RecordReference = {
  owner: string;
  projectId: string;
  id: string;
  digest: string;
  entityType?: string;
  entityId?: string;
};
export type HistorySelection =
  | { kind: "result"; scope: HistoryScope & { threadId: string; requestDigest: string }; operationId?: string; resultDigest?: string; record?: RecordReference; occurredAt?: string; title?: string }
  | { kind: "record"; scope: HistoryScope; record: RecordReference };
export type Currentness = { state: string; reasons: string[] };
export type HistoryRow = {
  id: string;
  kind: string;
  title: string;
  occurredAt: string;
  association: string;
  membership: string;
  availability: string;
  completion?: "CHECKPOINT" | "TERMINAL" | null;
  phase?: string | null;
  currentness: Currentness;
  selection: HistorySelection;
};
export type HistoryPage = {
  items: HistoryRow[];
  nextCursor: string | null;
  coverage: { association: string; scan: string; reasons: string[] };
  actorScope: string;
};
export type RestoreSelection = {
  projectId: string;
  entityType: string;
  entityId: string;
  targetDigest: string;
  expectedHead: string;
};
export type HistoryEntry = {
  selection: HistorySelection;
  title: string;
  occurredAt: string | null;
  kind: string;
  question: string | null;
  result: Record<string, unknown> | null;
  executionState?: string | null;
  resultPhase?: string | null;
  content: Record<string, unknown> | null;
  currentness: Currentness;
  availability: string;
  reasons: string[];
  currentHead: string | null;
  capability: { restore: string; applyReady: boolean; previewSupported: boolean; reasons: string[] };
  restoreSelection: RestoreSelection | null;
  technical: unknown;
};
export type RevisionChange = {
  path: string;
  before: unknown;
  after: unknown;
  beforeMissing: boolean;
  afterMissing: boolean;
  group: string;
  label: string;
};
export type RevisionComparison = { changes: RevisionChange[]; coverage: string; reasons: string[] };
export type RestorePreview = {
  selection: RestoreSelection;
  basisDigest: string;
  principalScope: string | null;
  availability: string;
  applyReady: boolean;
  profile: string;
  reasons: string[];
  comparison: RevisionComparison;
  impacts: { label: string; state: string }[];
  sourceChanges: string[];
  technical: unknown;
};
export type RestoreResult = {
  disposition: "APPLIED" | "NO_CHANGE";
  selection: RestoreSelection;
  newDigest: string | null;
  currentness: Currentness;
  operationId: string;
  technical: unknown;
};

export function selectionKey(selection: HistorySelection): string {
  return JSON.stringify(selection.kind === "result"
    ? [selection.kind, selection.scope.projectId, selection.scope.threadId, selection.scope.requestDigest, selection.operationId, selection.resultDigest, selection.record?.id]
    : [selection.kind, selection.scope.projectId, selection.scope.threadId, selection.scope.requestDigest, selection.record.owner, selection.record.id, selection.record.digest]);
}

export function restoreKey(selection: RestoreSelection): string {
  return JSON.stringify([selection.projectId, selection.entityType, selection.entityId, selection.targetDigest, selection.expectedHead]);
}
