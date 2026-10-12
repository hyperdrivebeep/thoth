import { t } from "./locale";
import type { MessageKey } from "./messages";

/** Client-owned errors keep their semantic key so changing display language does not freeze copy. */
export class UiError extends Error {
  constructor(readonly messageKey: MessageKey) {
    super(t(messageKey));
    Object.defineProperty(this, "message", {configurable: true, get: () => t(messageKey)});
  }
}
