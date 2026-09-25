import { Tag } from "@blueprintjs/core";
import { Disclosure } from "./Disclosure";

function label(key: string) { return key.replaceAll("_", " "); }

/** A bounded, inspectable projection; never infers scientific state from a shape or a count. */
export function RecordInspector({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="record-null">미기록</span>;
  if (typeof value === "boolean") return <Tag minimal intent={value ? "primary" : "none"}>{String(value)}</Tag>;
  if (typeof value !== "object") return <span className="record-value">{String(value)}</span>;
  if (depth > 3) return <Disclosure className="raw-detail" label="하위 데이터 펼치기"><pre>{JSON.stringify(value, null, 2)}</pre></Disclosure>;
  if (Array.isArray(value)) return value.length === 0 ? <span className="record-null">기록 없음</span> : (
    <div className="record-array">{value.slice(0, 100).map((item, index) => <div className="record-array-item" key={index}>
      <span className="record-index">{index + 1}</span><RecordInspector value={item} depth={depth + 1} />
    </div>)}{value.length > 100 && <Disclosure label={`나머지 ${value.length - 100}개 원문`}><pre>{JSON.stringify(value.slice(100), null, 2)}</pre></Disclosure>}</div>
  );
  const entries = Object.entries(value);
  return entries.length === 0 ? <span className="record-null">빈 응답 객체</span> : <dl className="record-grid">{entries.map(([key, item]) =>
    <div key={key}><dt>{label(key)}</dt><dd><RecordInspector value={item} depth={depth + 1} /></dd></div>,
  )}</dl>;
}
