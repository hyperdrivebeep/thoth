export const stateFormats = {
  investigateRow: {
    ko: (name: string) => `${name} 원인 조사`,
    en: (name: string) => `Investigate causes for ${name}`,
    "zh-Hans": (name: string) => `调查 ${name} 的原因`,
    "zh-Hant": (name: string) => `調查 ${name} 的原因`,
    ja: (name: string) => `${name} の原因を調べる`,
  },
  confirmRow: {
    ko: (name: string) => `${name} 판정 확인 기록 남기기`,
    en: (name: string) => `Record assessment confirmation for ${name}`,
    "zh-Hans": (name: string) => `记录对 ${name} 的评估确认`,
    "zh-Hant": (name: string) => `記錄對 ${name} 的評估確認`,
    ja: (name: string) => `${name} の判定の確認を記録する`,
  },
  rowEvidence: {
    ko: (name: string, n: number) => `${name} 근거 위치 ${n} 보기`,
    en: (name: string, n: number) => `View evidence location ${n} for ${name}`,
    "zh-Hans": (name: string, n: number) => `查看 ${name} 的第 ${n} 个证据位置`,
    "zh-Hant": (name: string, n: number) => `檢視 ${name} 的第 ${n} 個證據位置`,
    ja: (name: string, n: number) => `${name} の根拠の位置 ${n} を見る`,
  },
  criterionCounts: {
    ko: (total: number, met: number, failed: number, held: number) =>
      `기준 ${total}개 · 충족 ${met} · 미달 ${failed} · 보류 ${held}`,
    en: (total: number, met: number, failed: number, held: number) =>
      `${total} criteria · ${met} met · ${failed} unmet · ${held} held`,
    "zh-Hans": (total: number, met: number, failed: number, held: number) =>
      `${total} 条标准 · 满足 ${met} · 未满足 ${failed} · 暂缓 ${held}`,
    "zh-Hant": (total: number, met: number, failed: number, held: number) =>
      `${total} 條標準 · 滿足 ${met} · 未滿足 ${failed} · 暫緩 ${held}`,
    ja: (total: number, met: number, failed: number, held: number) =>
      `基準 ${total} 件 · 満たす ${met} · 満たさない ${failed} · 保留 ${held}`,
  },
  csvDownloaded: {
    ko: (loss: string) => `CSV 파일을 내려받았습니다. 이 파일에 담기지 않는 것: ${loss}`,
    en: (loss: string) => `CSV downloaded. This file does not include: ${loss}`,
    "zh-Hans": (loss: string) => `CSV 已下载。此文件不包含：${loss}`,
    "zh-Hant": (loss: string) => `CSV 已下載。此檔案不包含：${loss}`,
    ja: (loss: string) =>
      `CSV ファイルをダウンロードしました。このファイルに含まれないもの：${loss}`,
  },
  rowHistory: {
    ko: (name: string, action: string) => `${name} 판정 이력 ${action}`,
    en: (name: string, action: string) => `${action} assessment history for ${name}`,
    "zh-Hans": (name: string, action: string) => `${action} ${name} 的评估历史`,
    "zh-Hant": (name: string, action: string) => `${action} ${name} 的評估歷史`,
    ja: (name: string, action: string) => `${name} の判定履歴を${action}`,
  },
  staleMissing: {
    ko: (before: string) =>
      `이전 근거 기준: 이 가설이 나온 추적표 줄이 지금은 없습니다(이전: ${before})`,
    en: (before: string) =>
      `Earlier evidence basis — the originating trace row is no longer present (previously: ${before})`,
    "zh-Hans": (before: string) => `较早的证据依据 — 产生该假设的追溯行已不存在（此前：${before}）`,
    "zh-Hant": (before: string) => `較早的證據依據 — 產生該假設的追溯行已不存在（此前：${before}）`,
    ja: (before: string) =>
      `以前の根拠に基づく — この仮説のもとになった追跡表の行は、現在はありません（以前：${before}）`,
  },
  staleEvidence: {
    ko: (state: string) => `이전 근거 기준: 판정 상태는 같고 근거만 바뀌었습니다(판정: ${state})`,
    en: (state: string) =>
      `Earlier evidence basis — only the evidence changed; assessment remains ${state}`,
    "zh-Hans": (state: string) => `较早的证据依据 — 只有证据发生了变化；评估仍为${state}`,
    "zh-Hant": (state: string) => `較早的證據依據 — 只有證據發生了變化；評估仍為${state}`,
    ja: (state: string) =>
      `以前の根拠に基づく — 判定の状態は同じで、根拠だけが変わりました（判定：${state}）`,
  },
  staleVerdict: {
    ko: (before: string, after: string, flipped: boolean) =>
      `이전 근거 기준: 판정이 바뀌었습니다(이전: ${before} → 지금: ${after})${flipped ? " 충족과 미달이 뒤바뀌었습니다." : ""}`,
    en: (before: string, after: string, flipped: boolean) =>
      `Earlier evidence basis — assessment changed (${before} → ${after})${flipped ? " Met and unmet switched." : ""}`,
    "zh-Hans": (before: string, after: string, flipped: boolean) =>
      `较早的证据依据 — 评估已变化（${before} → ${after}）${flipped ? " 满足与未满足发生了互换。" : ""}`,
    "zh-Hant": (before: string, after: string, flipped: boolean) =>
      `較早的證據依據 — 評估已變化（${before} → ${after}）${flipped ? " 滿足與未滿足發生了互換。" : ""}`,
    ja: (before: string, after: string, flipped: boolean) =>
      `以前の根拠に基づく — 判定が変わりました（以前：${before} → 現在：${after}）${flipped ? " 「満たす」と「満たさない」が入れ替わりました。" : ""}`,
  },
  rechecked: {
    ko: (reason: string, who: string, note: string | null | undefined) =>
      `다시 확인됨: ${reason} · ${who}${note ? `: ${note}` : ""}`,
    en: (reason: string, who: string, note: string | null | undefined) =>
      `Rechecked — ${reason} · ${who}${note ? `: ${note}` : ""}`,
    "zh-Hans": (reason: string, who: string, note: string | null | undefined) =>
      `已重新核查 — ${reason} · ${who}${note ? `：${note}` : ""}`,
    "zh-Hant": (reason: string, who: string, note: string | null | undefined) =>
      `已重新核查 — ${reason} · ${who}${note ? `：${note}` : ""}`,
    ja: (reason: string, who: string, note: string | null | undefined) =>
      `再確認済み — ${reason} · ${who}${note ? `：${note}` : ""}`,
  },
  recheckRecorded: {
    ko: (reason: string, who: string) =>
      `이유 기록: ${reason} · ${who}. 이 가설은 이전 근거 기준으로 남습니다.`,
    en: (reason: string, who: string) =>
      `Reason recorded: ${reason} · ${who}. This hypothesis remains based on earlier evidence.`,
    "zh-Hans": (reason: string, who: string) =>
      `已记录原因：${reason} · ${who}。此假设仍基于较早的证据。`,
    "zh-Hant": (reason: string, who: string) =>
      `已記錄原因：${reason} · ${who}。此假設仍基於較早的證據。`,
    ja: (reason: string, who: string) =>
      `理由を記録しました：${reason} · ${who}。この仮説は以前の根拠に基づいたままです。`,
  },
  staleAction: {
    ko: (n: number, evidenceOnly: boolean) =>
      `이 행동이 기대는 가설 ${n}개가 이전 근거 기준입니다${evidenceOnly ? "(판정 상태는 같고 근거만 바뀜)" : ""}. 사람이 가설을 다시 확인하기 전에는 이 행동의 승인 요청과 결정, 실행이 막힙니다.`,
    en: (n: number, evidenceOnly: boolean) =>
      `${n} supporting ${n === 1 ? "hypothesis uses" : "hypotheses use"} earlier evidence${evidenceOnly ? " (assessment unchanged; only evidence changed)" : ""}. Approval requests, approval decisions, and execution remain blocked until a person rechecks the hypotheses.`,
    "zh-Hans": (n: number, evidenceOnly: boolean) =>
      `此行动所依赖的 ${n} 个假设使用的是较早的证据${evidenceOnly ? "（评估没有变化，只是证据变了）" : ""}。在有人重新核查这些假设之前，审批请求、审批决定和执行都会被阻止。`,
    "zh-Hant": (n: number, evidenceOnly: boolean) =>
      `此行動所依賴的 ${n} 個假設使用的是較早的證據${evidenceOnly ? "（評估沒有變化，只是證據變了）" : ""}。在有人重新核查這些假設之前，審批請求、審批決定和執行都會被阻止。`,
    ja: (n: number, evidenceOnly: boolean) =>
      `この行動が拠りどころにしている ${n} 件の仮説は、以前の根拠に基づいています${evidenceOnly ? "（判定の状態は同じで、根拠だけが変わりました）" : ""}。人が仮説を再確認するまで、この行動の承認リクエスト、承認の判断、実行はブロックされます。`,
  },
  supportCounts: {
    ko: (support: number | string, counter: number | string) =>
      `뒷받침 ${support} · 반박 ${counter}`,
    en: (support: number | string, counter: number | string) =>
      `Supporting ${support} · Refuting ${counter}`,
    "zh-Hans": (support: number | string, counter: number | string) =>
      `支持 ${support} · 反驳 ${counter}`,
    "zh-Hant": (support: number | string, counter: number | string) =>
      `支持 ${support} · 反駁 ${counter}`,
    ja: (support: number | string, counter: number | string) =>
      `裏づけ ${support} · 反証 ${counter}`,
  },
  today: {
    ko: (time: string) => `오늘 ${time}`,
    en: (time: string) => `Today ${time}`,
    "zh-Hans": (time: string) => `今天 ${time}`,
    "zh-Hant": (time: string) => `今天 ${time}`,
    ja: (time: string) => `今日 ${time}`,
  },
  yesterday: {
    ko: (time: string) => `어제 ${time}`,
    en: (time: string) => `Yesterday ${time}`,
    "zh-Hans": (time: string) => `昨天 ${time}`,
    "zh-Hant": (time: string) => `昨天 ${time}`,
    ja: (time: string) => `昨日 ${time}`,
  },
  monthDay: {
    ko: (month: number, day: number, time: string) => `${month}월 ${day}일 ${time}`,
    en: (month: number, day: number, time: string) =>
      `${new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(2000, month - 1, day)))} ${time}`,
    "zh-Hans": (month: number, day: number, time: string) => `${month}月${day}日 ${time}`,
    "zh-Hant": (month: number, day: number, time: string) => `${month}月${day}日 ${time}`,
    ja: (month: number, day: number, time: string) => `${month}月${day}日 ${time}`,
  },
  catalogChecked: {
    ko: (time: string, state: string) => `마지막 확인: ${time} · ${state}`,
    en: (time: string, state: string) => `Last checked: ${time} · ${state}`,
    "zh-Hans": (time: string, state: string) => `上次检查：${time} · ${state}`,
    "zh-Hant": (time: string, state: string) => `上次檢查：${time} · ${state}`,
    ja: (time: string, state: string) => `最終確認：${time} · ${state}`,
  },
  routeUnavailable: {
    ko: (reason: string) => `연결 경로를 사용할 수 없습니다 (${reason}).`,
    en: (reason: string) => `The connection route is unavailable (${reason}).`,
    "zh-Hans": (reason: string) => `连接途径不可用（${reason}）。`,
    "zh-Hant": (reason: string) => `連線途徑不可用（${reason}）。`,
    ja: (reason: string) => `接続経路を利用できません（${reason}）。`,
  },
  manualConnection: {
    ko: (guidance: string) =>
      `${guidance} 완료한 뒤 연결 상태를 다시 확인하세요. THOTH는 로그인을 시작하거나 완료하지 않았습니다.`,
    en: (guidance: string) =>
      `${guidance} Afterwards, refresh the connection status. THOTH has neither started nor completed login.`,
    "zh-Hans": (guidance: string) =>
      `${guidance} 完成之后，请刷新连接状态。THOTH 没有开始登录，也没有完成登录。`,
    "zh-Hant": (guidance: string) =>
      `${guidance} 完成之後，請重新整理連線狀態。THOTH 沒有開始登入，也沒有完成登入。`,
    ja: (guidance: string) =>
      `${guidance} 完了したら、接続状態を更新してください。THOTH はログインを開始も完了もしていません。`,
  },
  loginStarted: {
    ko: (isolated: boolean, guidance: string) =>
      `${isolated ? "THOTH 전용 Codex" : "계정"} 로그인 절차를 시작했습니다. 완료한 뒤 연결 상태를 다시 확인하세요. ${guidance}`,
    en: (isolated: boolean, guidance: string) =>
      `${isolated ? "THOTH-specific Codex" : "Account"} login started. Finish login, then refresh the connection status. ${guidance}`,
    "zh-Hans": (isolated: boolean, guidance: string) =>
      `已开始${isolated ? "THOTH 专用的 Codex" : "账户"}登录。请完成登录，然后刷新连接状态。${guidance}`,
    "zh-Hant": (isolated: boolean, guidance: string) =>
      `已開始${isolated ? "THOTH 專用的 Codex" : "帳戶"}登入。請完成登入，然後重新整理連線狀態。${guidance}`,
    ja: (isolated: boolean, guidance: string) =>
      `${isolated ? "THOTH 専用の Codex" : "アカウント"}のログイン手続きを開始しました。完了したら、接続状態を更新してください。${guidance}`,
  },
  elapsedTime: {
    ko: (time: string) => `걸린 시간 ${time}`,
    en: (time: string) => `Elapsed time ${time}`,
    "zh-Hans": (time: string) => `耗时 ${time}`,
    "zh-Hant": (time: string) => `耗時 ${time}`,
    ja: (time: string) => `所要時間 ${time}`,
  },
  modelTime: {
    ko: (time: string) => `모델 처리 시간 합 ${time}`,
    en: (time: string) => `Total model processing time ${time}`,
    "zh-Hans": (time: string) => `模型处理总时间 ${time}`,
    "zh-Hant": (time: string) => `模型處理總時間 ${time}`,
    ja: (time: string) => `モデルの処理時間の合計 ${time}`,
  },
  reusedStages: {
    ko: (reused: number, fresh: number) =>
      ` · 완료된 ${reused}단계 재사용 · 새로 부른 단계 ${fresh}`,
    en: (reused: number, fresh: number) =>
      ` · ${reused} completed stages reused · ${fresh} new stages called`,
    "zh-Hans": (reused: number, fresh: number) =>
      ` · 复用已完成的 ${reused} 个阶段 · 新调用的阶段 ${fresh} 个`,
    "zh-Hant": (reused: number, fresh: number) =>
      ` · 複用已完成的 ${reused} 個階段 · 新呼叫的階段 ${fresh} 個`,
    ja: (reused: number, fresh: number) =>
      ` · 完了済みの ${reused} 段階を再利用 · 新たに呼び出した段階 ${fresh}`,
  },
  autoRetries: {
    ko: (n: number) => ` · 자동 재시도 ${n}회(연결 끊김)`,
    en: (n: number) =>
      ` · ${n} automatic ${n === 1 ? "retry" : "retries"} (connection interrupted)`,
    "zh-Hans": (n: number) => ` · 自动重试 ${n} 次（连接中断）`,
    "zh-Hant": (n: number) => ` · 自動重試 ${n} 次（連線中斷）`,
    ja: (n: number) => ` · 自動再試行 ${n} 回（接続が切れたため）`,
  },
  usagePart: {
    ko: (label: string, tokens: string | null) =>
      ` · 그중 ${label} ${tokens === null ? "토큰 미확인" : `토큰 ${tokens}`}`,
    en: (label: string, tokens: string | null) =>
      ` · Including ${label}: ${tokens === null ? "tokens unknown" : `${tokens} tokens`}`,
    "zh-Hans": (label: string, tokens: string | null) =>
      ` · 其中 ${label}：${tokens === null ? "token 数未知" : `${tokens} token`}`,
    "zh-Hant": (label: string, tokens: string | null) =>
      ` · 其中 ${label}：${tokens === null ? "token 數未知" : `${tokens} token`}`,
    ja: (label: string, tokens: string | null) =>
      ` · うち ${label}：${tokens === null ? "トークン数は不明" : `${tokens} トークン`}`,
  },
  resultUsage: {
    ko: (tokens: string, time: string, reused: string, retried: string, parts: string) =>
      `이 조사: 토큰 ${tokens} · ${time}${reused}${retried}${parts} · 추정 비용 미확인`,
    en: (tokens: string, time: string, reused: string, retried: string, parts: string) =>
      `This investigation: ${tokens} tokens · ${time}${reused}${retried}${parts} · Estimated cost unknown`,
    "zh-Hans": (tokens: string, time: string, reused: string, retried: string, parts: string) =>
      `本次调查：${tokens} token · ${time}${reused}${retried}${parts} · 估计成本未知`,
    "zh-Hant": (tokens: string, time: string, reused: string, retried: string, parts: string) =>
      `本次調查：${tokens} token · ${time}${reused}${retried}${parts} · 估計成本未知`,
    ja: (tokens: string, time: string, reused: string, retried: string, parts: string) =>
      `この調査：${tokens} トークン · ${time}${reused}${retried}${parts} · 見積もりコストは不明`,
  },
  policyPeople: {
    ko: (policy: string, people: string | null) =>
      `규칙상: ${policy}${people ? ` · 필요한 사람: ${people}` : ""}`,
    en: (policy: string, people: string | null) =>
      `By policy: ${policy}${people ? ` · Required authority: ${people}` : ""}`,
    "zh-Hans": (policy: string, people: string | null) =>
      `按规则：${policy}${people ? ` · 所需权限：${people}` : ""}`,
    "zh-Hant": (policy: string, people: string | null) =>
      `按規則：${policy}${people ? ` · 所需權限：${people}` : ""}`,
    ja: (policy: string, people: string | null) =>
      `ルール上：${policy}${people ? ` · 必要な担当者：${people}` : ""}`,
  },
  riskTier: {
    ko: (tier: string) => `위험 등급: ${tier}`,
    en: (tier: string) => `Risk tier: ${tier}`,
    "zh-Hans": (tier: string) => `风险等级：${tier}`,
    "zh-Hant": (tier: string) => `風險等級：${tier}`,
    ja: (tier: string) => `リスクの階層：${tier}`,
  },
  ruleThreshold: {
    ko: (value: string, direction: string, unit: string, required: boolean) =>
      `${value} ${direction === ">=" ? "이상" : "이하"}${unit}${required ? "" : " · 필수 아님"}`,
    en: (value: string, direction: string, unit: string, required: boolean) =>
      `${direction === ">=" ? "At least" : "At most"} ${value}${unit}${required ? "" : " · Optional"}`,
    "zh-Hans": (value: string, direction: string, unit: string, required: boolean) =>
      `${direction === ">=" ? "至少" : "至多"} ${value}${unit}${required ? "" : " · 可选"}`,
    "zh-Hant": (value: string, direction: string, unit: string, required: boolean) =>
      `${direction === ">=" ? "至少" : "至多"} ${value}${unit}${required ? "" : " · 可選"}`,
    ja: (value: string, direction: string, unit: string, required: boolean) =>
      `${value}${unit} ${direction === ">=" ? "以上" : "以下"}${required ? "" : " · 必須ではない"}`,
  },
  notNumber: {
    ko: (value: string) => `${value}(숫자 아님)`,
    en: (value: string) => `${value} (not numeric)`,
    "zh-Hans": (value: string) => `${value}（不是数字）`,
    "zh-Hant": (value: string) => `${value}（不是數字）`,
    ja: (value: string) => `${value}（数値ではありません）`,
  },
} as const;
