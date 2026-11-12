import { createContext } from "react";

/**
 * The sources of the open project that were disconnected. The evidence list marks evidence that came
 * from one of them. It is handed down this way because the evidence reader itself makes only the
 * protected result requests and must not start reading the project's sources.
 */
export const DetachedSourcesContext = createContext<ReadonlySet<string>>(new Set());
