import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { setLocale } from "../i18n/locale";
import { ProjectForm } from "./ProjectForm";

const html = () => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}><ProjectForm onCreated={() => undefined} /></QueryClientProvider>);

it("starts with the task: a name and one primary button, with no slogan and no list of product features", () => {
  const markup = html();
  expect(markup).toContain("<h1>새 프로젝트</h1>");
  expect(markup).toContain("프로젝트 이름");
  // One filled button, and it is "create". The cutoff and profile fold is a plain text button.
  const buttons = [...markup.matchAll(/<button[^>]*class="([^"]*)"[^>]*>(.*?)<\/button>/g)].map(found => ({ classes: found[1], text: found[2].replace(/<[^>]+>/g, "") }));
  expect(buttons.filter(item => item.classes.includes("bp6-intent-primary")).map(item => item.text)).toEqual(["프로젝트 만들기"]);
  expect(buttons.find(item => item.text === "기준시점 · 고급 설정")!.classes).toContain("bp6-minimal");
  expect(markup).not.toContain("질문에서 근거로");
  expect(markup).not.toContain("start-principles");
  expect(markup).not.toContain("<h3");
});

it("has no slogan in the other languages either", () => {
  setLocale("en");
  try {
    const markup = html();
    expect(markup).toContain("<h1>New project</h1>");
    expect(markup).not.toContain("From questions to evidence");
  } finally {
    setLocale("ko");
  }
});

