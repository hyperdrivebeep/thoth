import { RpcTransportError } from "./rpcClient";

/** Only transport failures from an RPC read may retain the previous display. */
export function isTransientReadFailure(error: unknown): boolean {
  return error instanceof RpcTransportError && error.retryable;
}

export class ConversationRefreshError extends Error {
  constructor() {
    super("대화를 다시 확인하지 못했습니다. 잠시 후 다시 읽어주세요.");
    this.name = "ConversationRefreshError";
  }
}

export function canRetainConversation(error: unknown): boolean {
  return error instanceof ConversationRefreshError;
}
