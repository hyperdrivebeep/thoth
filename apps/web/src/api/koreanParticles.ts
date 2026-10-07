/** Korean particles that follow a word whose last sound is not known in advance (a number, a Latin name, a quoted phrase). */

// What can follow the last letter without changing how the word sounds: closing brackets and quotes, spaces and stops.
const TRAILING_MARKS = /[\s)\]}>」』）】〉》"'”’.,;:!?…]+$/u;
// 영·일·삼·육·칠·팔 end in a consonant; 이·사·오·구 do not. A number ending in 0 is spoken 십, 백, 천 or 만, which all do.
const DIGITS_WITH_BATCHIM = new Set(["0", "1", "3", "6", "7", "8"]);
// 엘·엠·엔·알 are the letter names that end in a consonant.
const LETTERS_WITH_BATCHIM = new Set(["l", "m", "n", "r"]);

/** Whether the text is spoken with a final consonant; null when its last letter says nothing (symbols only, other scripts). */
export function finalHasBatchim(text: string): boolean | null {
  const letters = Array.from(text.replace(TRAILING_MARKS, ""));
  const last = letters.pop();
  if (last === undefined) return null;
  const code = last.charCodeAt(0);
  if (code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28 !== 0;
  if (/^[0-9]$/.test(last)) return DIGITS_WITH_BATCHIM.has(last);
  // A lowercase word that ends in r is read as a word (레이더, 필터), not as the letter name 알; a capital R or SNR keeps the letter name.
  if (last === "r" && /^[A-Za-z]$/.test(letters[letters.length - 1] ?? "")) return false;
  if (/^[A-Za-z]$/.test(last)) return LETTERS_WITH_BATCHIM.has(last.toLowerCase());
  return null;
}

function withParticle(text: string, withBatchim: string, without: string): string {
  const batchim = finalHasBatchim(text);
  return batchim === null ? `${text}${withBatchim}(${without})` : `${text}${batchim ? withBatchim : without}`;
}

/** 을/를 */
export function eulreul(text: string): string {
  return withParticle(text, "을", "를");
}

/** 이/가 */
export function iga(text: string): string {
  return withParticle(text, "이", "가");
}

/** 과/와 */
export function gwawa(text: string): string {
  return withParticle(text, "과", "와");
}

/** 은/는 */
export function eunneun(text: string): string {
  return withParticle(text, "은", "는");
}
