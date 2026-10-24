import { useEffect, useRef, useState } from "react";
import type { ResultSelection } from "../api/resultEvidence";
import { useTranslation } from "../i18n/locale";
import { HypothesisCompareLive } from "./HypothesisCompareLive";
import { ResultEvidencePanel } from "./ResultEvidencePanel";

/** The parent keys this view by result identity and content, so focus cannot cross results. */
export function HypothesisEvidencePanel({ result, projectId, threadId, selection, citations }: {
  result: Record<string, unknown>; projectId: string; threadId: string;
  selection?: ResultSelection; citations?: Record<string, number>;
}) {
  const t = useTranslation();
  const [focus, setFocus] = useState<{ spanId: string } | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focus) return;
    panel.current?.focus({ preventScroll: true });
    const card = Array.from(panel.current?.querySelectorAll<HTMLElement>("[data-span-id]") ?? [])
      .find(item => item.getAttribute("data-span-id") === focus.spanId);
    (card ?? panel.current)?.scrollIntoView?.({ block: "nearest" });
  }, [focus]);
  return <>
    <HypothesisCompareLive result={result} projectId={projectId} threadId={threadId}
      onOpenEvidence={spanId => setFocus({ spanId })}/>
    {focus && <div className="hypothesis-basis-panel" ref={panel} tabIndex={-1} role="group" aria-label={t("evidence.selected")}>
      {selection ? <ResultEvidencePanel selection={selection} result={result} focusSpanId={focus.spanId}
        citations={citations} missingFocusMessage={t("expected.sourceUnavailable")}/>
        : <p role="alert">{t("detail.evidenceLinkMissing")}</p>}
    </div>}
  </>;
}
