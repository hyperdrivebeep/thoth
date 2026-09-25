import { Button, Callout } from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { createHistoryFixtureTransport, historyFixtureIds } from "../../api/historyTestFixture";
import { ResearchHistoryWorkspace } from "./ResearchHistoryWorkspace";

export default function HistoryExample() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const original = window.fetch;
    const fixture = createHistoryFixtureTransport({ applyReady: true });
    window.fetch = fixture.fetch;
    setReady(true);
    return () => { window.fetch = original; };
  }, []);
  return <main className="bp6-dark history-example-page">
    <Callout intent="warning" title="검증용 예시 · 실제 연구 데이터가 아닙니다">
      합성 기록으로 화면과 가상 복원 동작을 확인합니다. 서버·모델·운영 데이터는 호출하지 않습니다.
      <Button small minimal onClick={() => { window.location.search = ""; }}>실제 작업 화면으로</Button>
    </Callout>
    {ready && <ResearchHistoryWorkspace projectId={historyFixtureIds.project} threadId={historyFixtureIds.thread} />}
  </main>;
}
