import { applyDocumentLocale, t, useLocale } from "./i18n/locale";
import { lazy, Suspense, useEffect } from "react";

import { LiveProjectWorkspace } from "./components/LiveProjectWorkspace";

const HistoryExample = import.meta.env.DEV ? lazy(() => import("./components/history/HistoryExample")) : null;
const ResultExample = import.meta.env.DEV ? lazy(() => import("./components/ResultExample")) : null;

export function App() {
  const locale = useLocale();
  useEffect(() => applyDocumentLocale(locale), [locale]);
  const example = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("example");
  if (HistoryExample && example === "history") {
    return <Suspense fallback={<p role="status">{t("examples.historyLoading")}</p>}><HistoryExample /></Suspense>;
  }
  if (ResultExample && (example === "result-before" || example === "result-after" || example === "result-iris")) {
    return <Suspense fallback={<p role="status">{t("examples.resultLoading")}</p>}><ResultExample /></Suspense>;
  }
  return <LiveProjectWorkspace />;
}
