import { expect, it } from "vitest";
import type { ConnectedArtifact } from "../types";
import { activeBinding, connectedArtifacts, detachedArtifactIds, detachedArtifacts } from "./sourceBindings";
import { threadName } from "./threadName";

const artifact = (id: string) => ({ artifact_id: id, source_uri: "file:///" + id + ".pdf" }) as ConnectedArtifact;
const binding = (id: string, state: string) => ({ binding_id: "binding:" + id, artifact_id: id, state });

it("counts a source as connected only while its binding is active", () => {
  const sources = { artifacts: [artifact("a"), artifact("b"), artifact("c")], bindings: [binding("a", "ACTIVE"), binding("b", "DETACHED"), binding("c", "REVOKED")] };
  expect(connectedArtifacts(sources).map(item => item.artifact_id)).toEqual(["a"]);
  expect(detachedArtifacts(sources).map(item => item.artifact_id)).toEqual(["b", "c"]);
  expect([...detachedArtifactIds(sources)]).toEqual(["b", "c"]);
  expect(activeBinding(sources, "a")?.binding_id).toBe("binding:a");
  expect(activeBinding(sources, "b")).toBeUndefined();
});

it("keeps a source connected when the server listed no binding for it", () => {
  // A workspace read before bindings were listed, or an answer from an older server.
  expect(connectedArtifacts({ artifacts: [artifact("a")] }).map(item => item.artifact_id)).toEqual(["a"]);
  expect(connectedArtifacts({ artifacts: [artifact("a")], bindings: [] })).toHaveLength(1);
  expect(detachedArtifacts({ artifacts: [artifact("a")] })).toEqual([]);
  expect(activeBinding({ artifacts: [artifact("a")] }, "a")).toBeUndefined();
  expect(connectedArtifacts(undefined)).toEqual([]);
});

it("names a conversation by the name a person gave it, else by its first question", () => {
  expect(threadName({ display_name: "새 이름", problem: "첫 질문" })).toBe("새 이름");
  expect(threadName({ display_name: "  ", problem: "첫 질문" })).toBe("첫 질문");
  expect(threadName({ problem: "첫 질문" })).toBe("첫 질문");
  expect(threadName(undefined)).toBe("");
});

it("does not list a file as disconnected once the same file is connected again", () => {
  // Connecting a file again makes a new source row; the old row keeps its disconnected binding.
  const file = (id: string) => ({ ...artifact(id), source_uri: "file:///weather.txt", byte_sha256: "ab".repeat(32) }) as ConnectedArtifact;
  const sources = { artifacts: [file("old"), file("new"), artifact("other")], bindings: [binding("old", "DETACHED"), binding("new", "ACTIVE"), binding("other", "DETACHED")] };
  expect(connectedArtifacts(sources).map(item => item.artifact_id)).toEqual(["new"]);
  expect(detachedArtifacts(sources).map(item => item.artifact_id)).toEqual(["other"]);
  expect([...detachedArtifactIds(sources)]).toEqual(["other"]);
});
