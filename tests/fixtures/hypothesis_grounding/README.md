# Offline prediction-grounding replay data

These files are test inputs, not production records, model instructions or evidence of a real
experiment. They require no server, account, network request or local private directory.

`baseline.json` preserves a recorded synthetic result's four hypothesis statements, five tests,
twenty expected-result rows, empty basis lists, skipped table filling and zero dropped rows.
The source-byte digest records provenance; the payload digest seals this public projection.
The tests pin both the payload digest and the case-file digest. Changing a baseline requires
an explicit reviewed change, not automatic resealing when a test fails.

`cases.json` contains two positive controls and ten adverse categories. Each independently
labelled context contains structured observations, permitted span IDs and pinned source
identities. It has an accepted output and a rejected output so a grader that always passes or
always fails cannot satisfy the controls. The categories are numeric coincidence, condition,
unit, denominator, pinned revision, scope, invented span, future effect, counterevidence and
synthetic data presented as measurement.

The four role names are `OBSERVATION_RESTATEMENT`, `EVIDENCE_INFERENCE`, `HYPOTHESIS_ONLY` and
`UNASSESSED`. They do not confer verification authority. A forecast can have no direct supporting
observation and still be a valid unconfirmed hypothesis-only prediction. Old rows retain their
unassessed role and original links; empty links do not justify retroactive classification.

Run the controls or grade a separate candidate file:

```text
python scripts/hypothesis_grounding_replay.py check-fixtures
python scripts/hypothesis_grounding_replay.py grade --predictions predictions.json
python -m pytest tests/unit/test_hypothesis_grounding_replay.py
```

The candidate file is a JSON object keyed by **every** case ID. Each value follows the accepted
example's `Proposal` structure (`grounding_replay_proposal.v1`). Its bound references and
verification claim describe the output being evaluated; they do not let that output choose
its own permitted evidence or truth labels. Missing cases fail input validation. Grading returns
exit 0 only when every row passes, exit 1 for rejected output, and exit 2 for invalid CLI input.

The scorer compares structured facts and reference identities. It does not understand arbitrary
natural-language entailment, attach evidence, alter a database, validate causal effects or
implement a new product schema. Product integration and model-quality evaluation remain separate.
The assessment envelope's observation, origin and verification labels must come from an
independent interpretation of the displayed output. Feeding a model's unverified self-description
as those labels would not test whether its prose makes a false claim.
