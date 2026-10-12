import { t } from "../i18n/locale";
import { RpcTransportError } from "./rpcClient";

/** Only transport failures from an RPC read may retain the previous display. */
export function isTransientReadFailure(error: unknown): boolean {
  return error instanceof RpcTransportError && error.retryable;
}

export class ConversationRefreshError extends Error {
  constructor() {
    super(t("conversation.refreshFailed"));
    this.name = "ConversationRefreshError";
  }
}

export function canRetainConversation(error: unknown): boolean {
  return error instanceof ConversationRefreshError;
}
