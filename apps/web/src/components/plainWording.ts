import { plainReasons as reasons, builtinCriteria as builtIn } from "../i18n/plainWordingResources";
import { getLocale } from "../i18n/locale";

/** Wording for what the program itself records (the sufficiency reasons and the built-in criteria), in plain Korean.
 *  The recorded text is English and stays as it is; the screen shows these sentences and folds the original away. */



/** The Korean sentence for one recorded reason, or null when it is not one the program knows. */
export function plainReason(text: string): string | null {
  return reasons[text.trim()] ?? null;
}

const HANGUL = /[가-힣]/;
const LATIN_WORD = /[A-Za-z]{3,}/;

/** Text that people can read as it is: Korean (or only numbers and signs). English that nobody has worded is set aside. */
export function isReadable(text: string, responseLanguage?: string | null): boolean {
  return getLocale() === "en" || responseLanguage === "en" || HANGUL.test(text) || !LATIN_WORD.test(text);
}

export function splitReadable(items: string[], responseLanguage?: string | null): { plain: string[]; technical: string[] } {
  const plain: string[] = [];
  const technical: string[] = [];
  for (const item of items) {
    const known = plainReason(item);
    if (known) plain.push(known);
    else if (isReadable(item, responseLanguage)) plain.push(item);
    else technical.push(item);
  }
  return { plain, technical };
}

/** The built-in criteria (task profiles) record a short English target and question. */


export type CriterionText = { title: string | null; question: string | null; technical: string[] };

/** What a criteria row shows: a title and a question in Korean, with codes, ids and untranslated English kept for the folded technical part. */
export function criterionText(target: string, question: string, responseLanguage?: string | null): CriterionText {
  const known = builtIn[target];
  const technical: string[] = [];
  let title: string | null = null;
  let shownQuestion: string | null = null;
  if (known) {
    title = known.title;
    if (question === known.english) shownQuestion = known.question;
  } else if (HANGUL.test(target)) {
    title = target;
  } else if (target) {
    technical.push(target);
  }
  if (shownQuestion === null) {
    if (isReadable(question, responseLanguage)) shownQuestion = question || null;
    else technical.push(question);
  }
  return { title, question: shownQuestion, technical };
}
