import { afterEach, expect, it, vi } from "vitest";
import { rpc } from "./rpcClient";

afterEach(() => vi.unstubAllGlobals());

const reason = "REFERENCE_INQUIRY_NOT_FOUND";
const notice = `연구의 중간 기록은 저장됐습니다. 기준(reference) 단계만 실패했습니다 (사유: ${reason}). `
  + "결과는 기록에서 볼 수 있습니다. 같은 질문을 새로 보내면 연구가 다시 실행될 수 있으니 먼저 저장된 결과를 확인하세요. / "
  + `Intermediate research records were saved. Only the reference step failed (reason: ${reason}). `
  + "You can read the result in the history. Sending the same question again as a new request may run the research again, so check the saved result first.";

it("preserves the bilingual reference notice and saved-record metadata from an RPC error", async () => {
  const data = { reason_code: reason, research_saved: true };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    jsonrpc: "2.0", id: "notice", error: { code: -32030, message: notice, data },
  })));
  await expect(rpc("thread/input", { project_id: "p" }, "reference-notice")).rejects.toMatchObject({
    name: "RpcError", code: -32030, message: `${notice} [-32030]`, details: data,
  });
});

it("shows the operation id of an unexpected server failure, because the server log is found by it", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    jsonrpc: "2.0",
    id: "internal",
    error: { code: -32603, message: "internal command failure", data: { operation_id: "operation:42" } },
  })));
  await expect(rpc("thread/input", { project_id: "p" }, "internal-failure")).rejects.toMatchObject({
    name: "RpcError",
    code: -32603,
    message: "internal command failure [-32603] (operation:42)",
  });
});

it("adds no operation id to other errors, or when the server gave none", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(Response.json({
      jsonrpc: "2.0",
      id: "refused",
      error: { code: -32030, message: "refused", data: { operation_id: "operation:43" } },
    }))
    .mockResolvedValueOnce(Response.json({
      jsonrpc: "2.0",
      id: "bare",
      error: { code: -32603, message: "internal command failure" },
    }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(rpc("thread/input", { project_id: "p" }, "refused")).rejects.toMatchObject({
    message: "refused [-32030]",
  });
  await expect(rpc("thread/input", { project_id: "p" }, "bare")).rejects.toMatchObject({
    message: "internal command failure [-32603]",
  });
});
