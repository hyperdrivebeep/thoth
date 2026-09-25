export type ProjectSummary = {
  project_id: string;
  name: string;
  cutoff_at: string;
  lifecycle: string;
  overlay: string;
  policy_binding_ref: string;
  revision: number;
};

export type WorkThread = {
  thread_id: string;
  project_id: string;
  cycle_id: string;
  problem: string;
  lifecycle: string;
  execution_state: string;
  current_object_ids: string[];
  working_head_digest: string;
};

export type ConnectedArtifact = {
  artifact_id: string;
  project_id: string;
  source_uri: string;
  media_type: string;
  byte_sha256: string;
  authority_state: string;
  security_class: string;
  extraction_coverage: string;
  cutoff_state?: string;
};

export type EvidenceSpan = {
  span_id: string;
  artifact_id: string;
  source_version_id: string;
  exact_text: string;
  authority_state: string;
  cutoff_state: string;
  verification_state: string;
  support_state: string;
  locator: {
    page?: number | null;
    line?: number | null;
    paragraph?: number | null;
    sheet?: string | null;
    cell_range?: string | null;
    xml_path?: string | null;
    uri?: string | null;
  };
};

export type Hypothesis = {
  hypothesis_id: string;
  statement: string;
  primary_locus: string | null;
  uncertainty: string;
  support_evidence_refs: string[];
  counterevidence_queries: string[];
  predicted_observations: string[];
  discriminating_tests: Array<{
    procedure_candidate: string;
    expected_if_true: string;
    expected_if_alternative: string;
    risk_tier: string;
  }>;
};

export type ActionCandidate = {
  action_id: string;
  action_family: string;
  specification: string;
  expected_information_value: string;
  risk_tier: string;
  execution_authority: string;
  reversibility: string;
  state: string;
  source_refs: string[];
  missing_evidence: string[];
};

export type PackRunResult = {
  pack_id: string;
  scripted_model: boolean;
  evidence_count: number;
  selected_evidence_count: number;
  evidence: EvidenceSpan[];
  selected_evidence: EvidenceSpan[];
  project: { project_id: string; name: string; lifecycle: string };
  thread: { thread_id: string; problem: string; current_object_ids: string[] };
  cycle: {
    assessment: {
      assessment_id: string;
      derived_status: string[];
      missing_items: string[];
      scope_identity: { status: string; reason: string };
      criterion_authority: { status: string; reason: string };
      evidence_coverage: { status: string; reason: string };
      comparability: { status: string; reason: string };
    };
    portfolio: { portfolio_id: string; hypotheses: Hypothesis[] };
    action_plan: { plan_id: string; frontier: string[]; alternatives: ActionCandidate[] };
    commit: {
      disposition: string;
      committed_revision_ids: string[];
      receipt: {
        receipt_id: string;
        receipt_digest: string;
        claim_scopes: string[];
        integrity_state: string;
        provenance_state: string;
        semantic_truth_certified: boolean;
      };
    };
    memory_ids: string[];
    model_ids: string[];
    semantic_repair_attempted: boolean;
    outcome: {
      outcome_id: string;
      status: string;
      action_id: string;
      interpretation: string;
      limitations: string[];
    } | null;
  };
};

export type SemanticRevision = {
  revision_id: string;
  entity_type: string;
  entity_id: string;
  reason: string;
  revision_digest: string;
  parent_revision_digests: string[];
  evidence_refs: string[];
  created_at: string;
  actor: { actor_id: string; kind: string; role: string };
};

export type CriterionProjection = {
  head_digest: string;
  revision_id: string;
  value: {
    criterion_id: string;
    name: string;
    authority_state: string;
    evaluator_input_allowed: boolean;
    reference_candidate: boolean;
    evidence_refs: string[];
  };
};

export type MemoryRecord = {
  memory_id: string;
  kind: string;
  owner_revision_ref: string;
  source_ref: string | null;
  recall_eligibility: string;
  lifecycle: string;
  revision_digest: string;
};

export type ProtectedActionCard = {
  action_id: string;
  tool: string;
  environment: string;
  egress: string;
  target_revision: string;
  required_roles: string[];
  stop_conditions: string[];
};
