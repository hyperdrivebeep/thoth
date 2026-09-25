import { Button, Collapse } from "@blueprintjs/core";
import { useState, type ReactNode } from "react";

export function Disclosure({ label, children, className = "", defaultOpen = false }: {
  label: string;
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <div className={`toolkit-disclosure ${className}`.trim()}>
    <Button small minimal icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={() => setOpen(value => !value)}>{label}</Button>
    <Collapse isOpen={open}><div className="toolkit-disclosure-body">{children}</div></Collapse>
  </div>;
}
