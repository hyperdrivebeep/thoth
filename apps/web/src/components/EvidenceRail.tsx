import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { getLocale } from "../i18n/locale";
import { useEffect, useMemo, useRef, useState } from "react";
import { Callout, HTMLSelect, InputGroup } from "@blueprintjs/core";

import type { EvidenceSpan } from "../types";
import { StatusBadge } from "./StatusBadge";
import { eyebrowLabel } from "./statusLabels";

function locator(span: EvidenceSpan) {
  const values = [
    span.locator.page ? `p.${span.locator.page}` : null,
    span.locator.line ? `line ${span.locator.line}` : null,
    span.locator.sheet ?? null,
    span.locator.cell_range ?? null,
    span.locator.xml_path ?? null,
    span.locator.uri ?? null,
  ];
  return values.filter(Boolean).join(" · ") || t("evidenceRail.noLocator");
}

export function EvidenceRail({
  evidence,
  totalCount = evidence.length,
  loading = false,
  error,
  heading,
  focusSpanId,
  citations,
}: {
  focusSpanId?: string;
  citations?: Record<string, number>;
  evidence: EvidenceSpan[];
  totalCount?: number;
  loading?: boolean;
  error?: string;
  heading?: string;
}) {
  const [query, setQuery] = useState("");
  const [authority, setAuthority] = useState("ALL");
  const listRef = useRef<HTMLDivElement | null>(null);
  const filtered = useMemo(
    () =>
      evidence.filter(
        (span) =>
          span.span_id === focusSpanId ||
          (authority === "ALL" || span.authority_state === authority) &&
          (!query.trim() ||
            span.exact_text.toLowerCase().includes(query.trim().toLowerCase()) ||
            span.span_id.toLowerCase().includes(query.trim().toLowerCase())),
      ),
    [authority, evidence, query, focusSpanId],
  );
  useEffect(() => {
    if (!focusSpanId) return;
    const card = Array.from(listRef.current?.querySelectorAll<HTMLElement>("[data-span-id]") ?? [])
      .find(item => item.getAttribute("data-span-id") === focusSpanId);
    card?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [focusSpanId, evidence.length]);
  return (
    <aside className="evidence-rail" aria-label={t("evidenceRail.title")}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{eyebrowLabel("EVIDENCE")}</p>
          <h2>{loading ? t("evidenceRail.loading") : error ? t("evidenceRail.failed") : heading ?? formatMessage("activeEvidence", evidence.length)}</h2>
          {totalCount > evidence.length && <span>{formatMessage("evidenceTotal", totalCount.toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR"))}</span>}
        </div>
      </div>
      {evidence.length > 0 && (
        <div className="evidence-filters">
          <InputGroup
            leftIcon="search"
            aria-label={t("evidenceRail.search")}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("evidenceRail.searchText")}
            value={query}
          />
          <HTMLSelect
            aria-label={t("evidenceRail.authorityFilter")}
            onChange={(event) => setAuthority(event.target.value)}
            value={authority}
          >
            <option value="ALL">{t("evidenceRail.all")}</option>
            <option value="APPROVED">{t("sources.approved")}</option>
            <option value="OFFICIAL">{t("evidenceRail.official")}</option>
            <option value="INFORMAL">{t("evidenceRail.informal")}</option>
            <option value="UNCLASSIFIED">{t("evidenceRail.unclassified")}</option>
          </HTMLSelect>
        </div>
      )}
      <div className="evidence-list" ref={listRef}>
        {loading ? <p className="empty-copy">{t("evidenceRail.reading")}</p> : error ? <Callout compact intent="danger">{error}</Callout> : evidence.length === 0 ? (
          <p className="empty-copy">{t("evidenceRail.empty")}</p>
        ) : (
          filtered.map((span) => (
            <article className={`evidence-item${span.span_id === focusSpanId ? " focused" : ""}`} key={span.span_id} data-span-id={span.span_id}
              aria-current={span.span_id === focusSpanId ? "true" : undefined}>
              {citations?.[span.span_id] && <strong className="citation-number">[{citations[span.span_id]}]</strong>}
              <p>{span.exact_text}</p>
              <code>{locator(span)}</code>
              <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{span.artifact_id} · {span.span_id}</small></details>
              <div className="badge-row">
                <StatusBadge value={span.authority_state} />
                <StatusBadge value={span.cutoff_state} />
                <StatusBadge value={span.support_state} />
              </div>
            </article>
          ))
        )}
        {evidence.length > 0 && filtered.length === 0 && (
          <p className="empty-copy">{t("evidenceRail.noMatch")}</p>
        )}
      </div>
    </aside>
  );
}
