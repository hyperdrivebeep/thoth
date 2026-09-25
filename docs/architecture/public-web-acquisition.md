# Public acquisition and anonymous rendering

`PUBLIC_WEB` is a registered connector factory, opt-in through connector configuration and
the existing authoritative project allowlist/egress policy. It does not add a paid provider,
account, cookie import, private-address exception, or login automation.

Selectors use `mode: READ` with `uri`, or `mode: SEARCH` with `query`. Search requires
`enable_public_search: true` and `html.duckduckgo.com` in `allowed_public_hosts`. Discovered links
are candidates only. Each selected URI is fetched through the authorized reader before ingestion.
All final sources must also belong to the explicitly allowed host set.

Automatic selectors also carry the classification of their input context. Free-form research
input starts at INTERNAL; any more restricted source raises that classification. The project's
`max_query_egress_security_class` defaults to PUBLIC. An administrator must explicitly permit a
higher disclosure ceiling before automatic queries derived from that context leave the project.
This is separate from the incoming document's classification and the host allowlist. A denied
query does not dispatch the reader and does not stop independent connected-source work.

The HTTPS reader rejects non-public DNS results and pins the validated address while preserving
TLS hostname verification. Every redirect is validated again. It does not use ambient proxies,
cookies or credentials. Bodies are bounded; unsupported status, compression or media is explicit.
Retrieval time does not establish that content predates the project cutoff: unknown-time sources
remain unknown unless the existing authority/cutoff contract establishes eligibility.

When acquired HTML is a dynamic shell, a configured `allow_anonymous_browser: true` route can
render it in a fresh Chromium context. Install the optional `browser` dependencies and Chromium
for that route. Browser requests are fulfilled through the same bounded reader; service workers,
WebSockets, non-GET requests and downloads are blocked. Resource count, aggregate bytes, DOM size,
render time and cleanup are limited. No browser is required for ordinary static source ingestion.
The connector's configured timeout is consumed by the reader and anonymous renderer.

The original HTTP bytes are retained as `provenance-only`, coverage NONE and no evidence spans.
The rendered artifact inherits its source scope. A typed WEB_TRANSFORMATION control record links
HTTP/rendered URI, time, hashes and artifact IDs alongside the ordinary acquisition receipt. It
does not certify semantic correctness. The HTML parser itself executes no JavaScript or network.

Actual discover, fetch and close observations use bounded awaits and integer millisecond timing.
Cleanup is attempted even after the research budget or ownership fence closes. Remote termination
is not inferred from local cancellation. Uncooperative driver cancellation is retained as UNKNOWN.

Evidence for the implementation lives in `docs/verification/repair-u06-u09-20260913.md`; controlled
browser, public HTTP and live model evidence are separate.

2026-09-14: WebPage navigation and WebTransformation 1.1 bind the broker-verified main-document
URI, HTTP redirects, document navigations, document hashes and captured DOM location. History-only
URL changes are separate location metadata. DOM and document identity are captured together;
a document change during capture is held. Validated HTTP redirects use explicit fresh document
navigation so Chromium must traverse the broker again; other-document bytes are never silently
served as the old document. Redirected subresources are explicitly unsupported and held.
Source URI/locator and transformation use the final verified document; original HTTP stays provenance.

Chromium's native document loader ID distinguishes document replacement from same-document
history changes. The captured DOM is bracketed by native loader reads; page scripts cannot supply
the document identity. A new unverified document such as about:blank is held rather than attributed
to the preceding HTTPS source. Previously verified loader IDs can retain their own provenance.
