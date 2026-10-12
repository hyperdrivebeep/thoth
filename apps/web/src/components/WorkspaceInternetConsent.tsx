import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { rpc } from "../api/rpcClient";

const STATE_TEXT: Record<string, string> = { get ALLOWED() { return t("web.allowed"); }, get DENIED() { return t("web.denied"); } };

/** The workspace's "public web lookup" consent in the settings page: the same choice and the same RPC as the second
 * step of the first setup, for a workspace that already has projects and no consent saved. */
export function WorkspaceInternetConsent({ consent, focus = false, onFocused }: { consent: string; focus?: boolean; onFocused?: () => void }) {
  useTranslation();
  const client = useQueryClient();
  const card = useRef<HTMLElement | null>(null);
  const update = useMutation({
    mutationFn: (internet_consent: string) => rpc("workspace/setup/update", { internet_consent }, crypto.randomUUID()),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: ["workspace-ready"] }), client.invalidateQueries({ queryKey: ["workspace-setup"] })]);
    },
  });
  useEffect(() => {
    if (!focus) return;
    card.current?.scrollIntoView?.({ block: "center" });
    card.current?.querySelector("button")?.focus();
    onFocused?.();
  }, [focus, onFocused]);
  return <section className="detail-card" id="workspace-internet-consent" ref={card} aria-labelledby="workspace-internet-consent-title">
    <h2 id="workspace-internet-consent-title">{t("web.lookup")}</h2>
    <p>{message("currentSetting", STATE_TEXT[consent] ?? t("web.undecided"))}</p>
    <p className="muted">{t("setup.localDisclosure")}</p>
    <div className="first-run-choices">
      <Button icon="globe" intent={consent === "ALLOWED" ? "primary" : "none"} aria-pressed={consent === "ALLOWED"} loading={update.isPending} onClick={() => update.mutate("ALLOWED")}>{t("setup.allow")}</Button>
      <Button icon="disable" intent={consent === "DENIED" ? "primary" : "none"} aria-pressed={consent === "DENIED"} loading={update.isPending} onClick={() => update.mutate("DENIED")}>{t("setup.deny")}</Button>
    </div>
    <p className="muted">{t("web.consentScope")}</p>
    {update.error && <Callout compact intent="danger" role="alert">{update.error.message}</Callout>}
  </section>;
}
