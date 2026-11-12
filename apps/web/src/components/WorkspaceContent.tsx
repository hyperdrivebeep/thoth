import { useContext, type ReactNode, type Ref } from "react";
import { WorkspacePageContext, workspaceTabIds } from "./workspaceTabs";

/** The main content of the current workspace page, announced as the panel of the selected tab. */
export function WorkspaceContent({
  className,
  children,
  ref,
}: {
  className?: string;
  children: ReactNode;
  ref?: Ref<HTMLElement>;
}) {
  const page = useContext(WorkspacePageContext);
  const ids = page ? workspaceTabIds(page) : null;
  return (
    <section
      ref={ref}
      className={["workspace-content", className].filter(Boolean).join(" ")}
      role={ids ? "tabpanel" : undefined}
      id={ids?.panel}
      aria-labelledby={ids?.tab}
    >
      {children}
    </section>
  );
}
