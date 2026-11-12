import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { LOCAL_RETRIEVAL_INSTALL_COMMAND } from "./localRetrievalInstall";

const install = readFileSync(
  fileURLToPath(new URL("../../../../docs/INSTALL.md", import.meta.url)),
  "utf8",
).split(/\r?\n/);

it("shows the same install command in docs/INSTALL.md as the settings card", () => {
  expect(install).toContain(LOCAL_RETRIEVAL_INSTALL_COMMAND);
});

it("is the basic install command from INSTALL.md plus the local-retrieval extra", () => {
  const basic = install.find(
    (line) => line.startsWith("uv sync ") && line.endsWith("--extra dev --dev"),
  );
  expect(basic).toBeDefined();
  expect(LOCAL_RETRIEVAL_INSTALL_COMMAND).toBe(`${basic} --extra local-retrieval`);
});
