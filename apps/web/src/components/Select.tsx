import { HTMLSelect, type HTMLSelectProps } from "@blueprintjs/core";
import { forwardRef } from "react";

/**
 * The select of the component library without the tooltip its arrow carries. The library writes "Open dropdown"
 * into the arrow's SVG title in English whatever the screen language, and a browser shows that as a tooltip and a
 * screen reader reads it. The select has its own label, so the arrow is decoration.
 */
export const Select = forwardRef<HTMLSelectElement, HTMLSelectProps>(function Select(props, ref) {
  return <HTMLSelect {...props} ref={ref} iconProps={{ ...props.iconProps, title: null }} />;
});
