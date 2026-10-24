/** Synthetic display data only; no measured claim or real research result. */
export const groundingFixture = {
  hypothesis_grounding_diagnostics: [{ reason: "UNKNOWN_SPAN" }],
  portfolio: { hypotheses: [1, 2, 3, 4, 5].map(number => ({
    hypothesis_id: `h${number}`, statement: `Synthetic candidate ${number}`, uncertainty: "Not confirmed",
    discriminating_tests: number === 1 ? [{ test_id: "t1", procedure_candidate: "Synthetic comparison",
      expected_by_hypothesis: [
        { hypothesis_id: "h1", knowledge_state: "PREDICTED", expected: "Synthetic recorded observation: 16/20", basis: ["span:a"], binding_availability: "AVAILABLE",
          grounding: { schema_version: "1.0.0", derivation_role: "OBSERVATION_RESTATEMENT", rationale: "Synthetic observation, not a new experiment." } },
        { hypothesis_id: "h2", knowledge_state: "PREDICTED", expected: "Synthetic inferred outcome", basis: ["span:b"], binding_availability: "AVAILABLE",
          grounding: { schema_version: "1.0.0", derivation_role: "EVIDENCE_INFERENCE", rationale: "The synthetic observation supplies a premise, not proof of this prediction." } },
        { hypothesis_id: "h3", knowledge_state: "PREDICTED", expected: "Synthetic conditional prediction", basis: [], binding_availability: "AVAILABLE",
          grounding: { schema_version: "1.0.0", derivation_role: "HYPOTHESIS_ONLY", rationale: "" } },
        { hypothesis_id: "h4", knowledge_state: "UNKNOWN", expected: null, basis: [], binding_availability: "AVAILABLE",
          grounding: { schema_version: "1.0.0", derivation_role: "UNASSESSED", rationale: "" } },
        { hypothesis_id: "h5", knowledge_state: "PREDICTED", expected: "Synthetic legacy prediction", basis: ["span:old"], binding_availability: "UNAVAILABLE", grounding_projection: "LEGACY_UNASSESSED",
          grounding: { schema_version: "1.0.0", derivation_role: "UNASSESSED", rationale: "" } },
      ] }] : [],
  })) },
};
