import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ProjectSummary } from "../types";
import { ProjectLanding } from "./LiveProjectWorkspace";

const project = {
  project_id: "project:rocket",
  name: "rocket live review",
  lifecycle: "DRAFT",
  overlay: "general-rnd",
  cutoff_at: "2026-09-23T00:00:00Z",
  revision: 1,
} as ProjectSummary;

describe("ProjectLanding", () => {
  it("keeps existing projects primary when data arrives after the initial loading render", () => {
    const loading = renderToStaticMarkup(<ProjectLanding projects={[]} loading onOpen={()=>undefined} onCreated={()=>undefined} />);
    expect(loading).toContain("프로젝트를 읽는 중");
    expect(loading).not.toContain("프로젝트 이름");

    const loaded = renderToStaticMarkup(<ProjectLanding projects={[project]} onOpen={()=>undefined} onCreated={()=>undefined} />);
    expect(loaded).toContain("rocket live review");
    expect(loaded).toContain("프로젝트 열기");
    expect(loaded).not.toContain("프로젝트 이름");
  });
});
