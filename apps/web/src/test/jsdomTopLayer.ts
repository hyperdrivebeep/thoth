// jsdom answers the top-layer selectors that floating-ui asks about (":popover-open", ":modal") by
// failing to parse them, and each failed parse is very slow when the page holds an open popover:
// one popover open cost about 13 seconds of selector matching. Browsers support both selectors, and
// jsdom has no top layer, so the answer to both is always "no". Answering it directly is the same
// result without the slow failure.
const TOP_LAYER_SELECTORS = new Set([":popover-open", ":modal"]);

export function answerTopLayerSelectorsWithNo(): () => void {
  const real = Element.prototype.matches;
  Element.prototype.matches = function (this: Element, selectors: string): boolean {
    return TOP_LAYER_SELECTORS.has(selectors) ? false : real.call(this, selectors);
  };
  return () => { Element.prototype.matches = real; };
}
