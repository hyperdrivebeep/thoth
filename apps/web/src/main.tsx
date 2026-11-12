import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { ThothBlueprint } from "./components/ThothBlueprint";
import { watchFocus } from "./components/returnFocus";
import "@blueprintjs/core/lib/css/blueprint.css";
import "@blueprintjs/icons/lib/css/blueprint-icons.css";
import "./styles.css";
import "./workstation.css";

const queryClient = new QueryClient();
watchFocus();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThothBlueprint>
        <App />
      </ThothBlueprint>
    </QueryClientProvider>
  </StrictMode>,
);
