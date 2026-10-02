import { objectList, textValue } from "../api/presentation";

export type Estimate = { dimension: string; band: string; estimator_type: string; basis_text: string };

export function parseEstimates(value: unknown): Estimate[] {
  return objectList(value).map(item => ({ dimension: textValue(item.dimension), band: textValue(item.band),
    estimator_type: textValue(item.estimator_type), basis_text: textValue(item.basis_text) }));
}
