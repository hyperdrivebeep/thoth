import { t } from "../i18n/locale";
import type { CoverageMatrix, CoverageMatrixRow, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { builtinCriteria } from "../i18n/plainWordingResources";
import { criterionText, isReadable, plainReason } from "./plainWording";
import { coverageTermLabel } from "./statusLabels";
import type { TechnicalGroup } from "./TechnicalDetails";

/** Server progress keys with a user wording; anything else that looks like an internal id is only counted. */
const progressTerms: Record<string, string> = {
  get "result:terminal"() { return t("followup.terminal"); },
  get "result:checkpoint"() { return t("followup.checkpoint"); },
  get requirements_recorded() { return t("followup.requirements"); },
  get coverage_assessed() { return t("followup.coverage"); },
  get RESULT_NOT_PRODUCED() { return t("followup.notProduced"); },
  get LEGACY_BASIS_UNKNOWN() { return t("followup.legacyBasis"); },
  get REQUIREMENT_SET_UNAVAILABLE() { return t("followup.requirementsUnavailable"); },
  get COVERAGE_UNAVAILABLE() { return t("followup.coverageUnavailable"); },
  get REQUIREMENT_NOT_ASSESSED() { return t("followup.unassessed"); },
};
const STAGE_ID = /^research-stage:/;
const REQUIREMENT_ID = /^requirement:/;
// An ASCII token with no spaces that carries a separator or is all capitals is an internal code, not a sentence.
const CODE_LIKE = /^(?=.*[_:])[A-Za-z0-9_:.-]+$|^[A-Z][A-Z0-9]+$/;
// What the program records for a requirement that is still open: "Resolve " and the requirement's question
// (requirement_compiler.py). The question is a built-in criterion's English wording, or what the person asked.
const OPEN_REQUIREMENT = /^Resolve ([\s\S]+)$/;

/** The open requirement a recorded follow-up names: a built-in criterion by its title in the screen language, or the question as it was asked. */
function openRequirement(item: string): { text: string; builtIn: boolean } | null {
  const question = OPEN_REQUIREMENT.exec(item)?.[1].trim();
  if (!question) return null;
  const known = Object.values(builtinCriteria).find(criterion => criterion.english === question);
  return known ? { text: known.title, builtIn: true } : { text: question, builtIn: false };
}

function listItemLabel(item: string): string | null {
  if (progressTerms[item]) return progressTerms[item];
  const term = coverageTermLabel(item);
  return term !== item ? term : null;
}

/** A server list split into what is shown in words and what is only counted (ids, codes, untranslated text). */
export function splitItems(items: string[], responseLanguage?: string | null) {
  const stages = items.filter(item => STAGE_ID.test(item));
  const requirements = items.filter(item => REQUIREMENT_ID.test(item));
  const rest = items.filter(item => !STAGE_ID.test(item) && !REQUIREMENT_ID.test(item));
  const plain: string[] = [];
  const codes: string[] = [];
  const sentences: string[] = [];
  const originals: string[] = [];
  for (const recorded of rest) {
    const open = openRequirement(recorded);
    if (open) {
      // A built-in criterion by its title, or the question in the person's own words (readable whatever its language).
      // A bare code (a field name of a criterion) stays a code. The recorded line is kept for the folded technical part.
      if (!open.builtIn && CODE_LIKE.test(open.text)) codes.push(recorded);
      else { plain.push(open.text); originals.push(recorded); }
      continue;
    }
    const item = recorded;
    const label = listItemLabel(item) ?? plainReason(item);
    if (label) { plain.push(label); if (plainReason(item)) originals.push(item); }
    else if (CODE_LIKE.test(item)) codes.push(item);
    else if (isReadable(item, responseLanguage)) plain.push(item);
    else sentences.push(item); // English the program wrote: shown only in the folded technical part
  }
  return { plain, stages, requirements, codes, sentences, technical: [...stages, ...requirements, ...codes, ...sentences, ...originals] };
}

export const linkedRefs = (rows: CoverageMatrixRow[]) => Array.from(new Set(rows.flatMap(row => row.evidence_refs)));

/** The title a checks-table row is shown under, and the ids and codes behind it. */
export function rowWording(row: CoverageMatrixRow, responseLanguage?: string | null) {
  const text = criterionText(row.target, row.question, responseLanguage);
  const term = coverageTermLabel(row.target);
  const title = text.title ?? (term !== row.target ? term : null);
  const technical = [...text.technical.filter(item => !(title !== null && item === row.target)), ...row.evidence_refs.slice(0, 3), ...row.reason_codes.slice(0, 3)];
  return { text, title, technical };
}

/** An action the person has to take about this answer; "none" is a value, not an action. */
export function requiredAction(action?: NextUserAction | null, progress?: UserProgressSummary | null): NextUserAction | null {
  return action ?? progress?.next_user_action ?? null;
}

/**
 * The ids, codes and untranslated originals behind the review summary, one group per part. The card
 * shows them in its single technical block (TechnicalDetails) instead of one folded block per part.
 */
export function followupTechnical({ progress, coverage, action }: {
  progress?: UserProgressSummary | null;
  coverage?: CoverageMatrix | null;
  action?: NextUserAction | null;
}, responseLanguage?: string | null): TechnicalGroup[] {
  const rows = coverage?.rows ?? [];
  const next = requiredAction(action, progress);
  const groups: TechnicalGroup[] = [];
  if (progress) {
    groups.push({ label: t("followup.reviewSummary"), items: splitItems(progress.progress_items, responseLanguage).technical });
    groups.push({ label: t("followup.recordedChecks"), items: splitItems(progress.recorded_checks, responseLanguage).technical });
    groups.push({ label: t("followup.remaining"), items: splitItems(progress.remaining_gaps, responseLanguage).technical });
    groups.push({ label: t("followup.unverifiable"), items: splitItems(progress.unknowns, responseLanguage).technical });
  }
  groups.push({ label: t("followup.linkedEvidence"), items: linkedRefs(rows) });
  groups.push({ label: t("followup.nextAction"), items: next ? [next.label] : [] });
  rows.forEach((row, index) => {
    const { title, technical } = rowWording(row, responseLanguage);
    groups.push({ label: index + 1 + ". " + (title ?? t("followup.assessmentCriterion")), items: technical });
  });
  return groups;
}
