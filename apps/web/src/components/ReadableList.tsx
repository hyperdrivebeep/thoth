import { plainReason, splitReadable } from "./plainWording";

/** A list of texts the program or the model recorded: readable ones are listed, untranslated English is only counted and folded away. */
export function ReadableList({ items }: { items: string[] }) {
  const { plain, technical } = splitReadable(items);
  const folded = [...items.filter(item => plainReason(item) !== null), ...technical];
  return <>
    {plain.length > 0 && <ul>{plain.map((item, index) => <li key={index}>{item}</li>)}</ul>}
    {technical.length > 0 && <p>기록된 상세 문구 {technical.length}개</p>}
    {folded.length > 0 && <details className="connection-tech"><summary>기술 정보</summary><small>{folded.join(" · ")}</small></details>}
  </>;
}
