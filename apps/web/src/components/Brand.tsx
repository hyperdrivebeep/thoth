import { Icon, Tag } from "@blueprintjs/core";
import { t } from "../i18n/locale";
import { eyebrowLabel } from "./statusLabels";

/** The product name at the top of the project list, with where this copy runs (this computer or the review site). */
export function Brand({ hosted }: { hosted: boolean }) {
  return (
    <div className="brand">
      <div className="brand-name">
        <div className="brand-mark">
          <Icon icon="layout-grid" size={20} />
        </div>
        <div>
          <strong>THOTH</strong>
          <span>{t("brand.tagline")}</span>
        </div>
      </div>
      <Tag minimal>{eyebrowLabel(hosted ? "REVIEW WORKSPACE" : "LOCAL WORKSPACE")}</Tag>
    </div>
  );
}
