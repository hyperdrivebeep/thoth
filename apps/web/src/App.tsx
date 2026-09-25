import { lazy, Suspense } from "react";

import { LiveProjectWorkspace } from "./components/LiveProjectWorkspace";

const HistoryExample = import.meta.env.DEV ? lazy(() => import("./components/history/HistoryExample")) : null;
const ResultExample = import.meta.env.DEV ? lazy(() => import("./components/ResultExample")) : null;

export function App() {
  const example = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("example");
  if (HistoryExample && example === "history") {
    return <Suspense fallback={<p role="status">이력 화면 예시를 여는 중…</p>}><HistoryExample /></Suspense>;
  }
  if (ResultExample && (example === "result-before" || example === "result-after")) {
    return <Suspense fallback={<p role="status">결과 화면 예시를 여는 중…</p>}><ResultExample /></Suspense>;
  }
  return <LiveProjectWorkspace />;
}
