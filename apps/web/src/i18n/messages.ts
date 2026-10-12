import { finishingMessages } from "./finishingMessages";
import { remainingMessages } from "./remainingMessages";
import { apiNoticesMessages } from "./apiNoticesMessages";
import { advancedMessages } from "./advancedMessages";
import { reviewPanelsMessages } from "./reviewPanelsMessages";
import { activityMessages } from "./activityMessages";
import { hypothesisActionsMessages } from "./hypothesisActionsMessages";
import { recordDialogsMessages } from "./recordDialogsMessages";
import { recordPanelsMessages } from "./recordPanelsMessages";
import { researchPanelsMessages } from "./researchPanelsMessages";
import { modelHelpMessages } from "./modelHelpMessages";
import { historyViewMessages } from "./historyViewMessages";
import { judgmentMessages } from "./judgmentMessages";
import { followupMessages } from "./followupMessages";
import { commonStateMessages } from "./commonStateMessages";
import { oauthMessages } from "./oauthMessages";
import { sourceMessages } from "./sourceMessages";
import { traceMessages } from "./traceMessages";
import { researchMessages } from "./researchMessages";
import { credentialMessages } from "./credentialMessages";
import { workspaceDetailMessages } from "./workspaceDetailMessages";
import { workspaceMessages } from "./workspaceMessages";
import { setupMessages } from "./setupMessages";
/** Whole UI messages. Research text and canonical status codes never pass through this catalog. */
export const messages = {
  "usage.memoryExpansion": { ko: "기억 검색어 넓히기", en: "Memory query expansion" },
  ...finishingMessages,
  ...remainingMessages,
  ...apiNoticesMessages,
  ...advancedMessages,
  ...reviewPanelsMessages,
  ...activityMessages,
  ...hypothesisActionsMessages,
  ...recordDialogsMessages,
  ...recordPanelsMessages,
  ...researchPanelsMessages,
  ...modelHelpMessages,
  ...historyViewMessages,
  ...judgmentMessages,
  ...followupMessages,
  ...commonStateMessages,
  ...oauthMessages,
  ...sourceMessages,
  ...traceMessages,
  ...researchMessages,
  ...credentialMessages,
  ...workspaceDetailMessages,
  ...workspaceMessages,
  ...setupMessages,
  "language.display": { ko: "화면 언어", en: "Display language" },
  "language.reply": { ko: "다음 질문의 답변 언어", en: "Response language for the next question" },
  "language.automatic": { ko: "질문 언어에 맞춤", en: "Match the question language" },
  "language.fixed": { ko: "보낸 요청은 원래 언어로 이어갑니다. 변경은 다음 질문부터 적용됩니다.", en: "Submitted requests keep their original language. Changes apply to your next question." },
  "language.accepted": { ko: "접수된 요청의 답변 언어", en: "Response language of the accepted request" },
  "language.proseFlag": { ko: "일부 새 가설·예상 결과에 요청한 영어와 다른 문구가 있습니다. 원문은 보존했으며 자동으로 다시 생성하지 않았습니다.", en: "Some new hypothesis or expected-result text does not match the requested English. The original text is preserved; no automatic regeneration was triggered." },
  "language.cost": { ko: "새 질문에는 모델 사용량이 듭니다. 화면 언어만 바꾸면 모델을 호출하지 않습니다.", en: "New questions use model capacity. Changing only the display language makes no model call." },
  "language.unsupported": { ko: "이 서버는 답변 언어 선택을 지원하지 않습니다.", en: "This server does not support selecting a response language." },
  "expected.title": { ko: "각 가설의 예상 결과(AI 제안)", en: "Expected results for each hypothesis (AI proposal)" },
  "expected.unknown": { ko: "모름", en: "Unknown" },
  "expected.unconfirmed": { ko: "자료로 확인되지 않은 예상", en: "Prediction not confirmed by evidence" },
  "expected.notice": { ko: "모델이 낸 제안이며 사람이 확인하기 전에는 미확정입니다. 같은 말은 같은 결과로 봅니다. 확률이나 점수가 아닙니다.", en: "These model proposals remain unconfirmed until reviewed by a person. Matching labels represent the same result. They are not probabilities or scores." },
  "composer.problem": { ko: "현재 막힌 문제", en: "What are you working on?" },
  "composer.followupPlaceholder": { ko: "추가 질문, 새 근거, 바뀐 조건을 적어주세요…", en: "Add a question, new evidence, or changed conditions…" },
  "composer.startPlaceholder": { ko: "어떤 문제를 해결하고 싶으신가요?", en: "What problem would you like to investigate?" },
  "composer.attach": { ko: "자료 연결", en: "Connect sources" },
  "composer.followup": { ko: "지시 추가", en: "Send follow-up" },
  "composer.start": { ko: "첫 조사 시작", en: "Start research" },
  "composer.checkOriginal": { ko: "원 요청 확인", en: "Check original request" },
  "composer.retryOriginal": { ko: "이전 요청 같은 key로 확인", en: "Check the original submission" },
  "composer.checking": { ko: "원 요청의 접수 상태를 확인하는 중입니다. 초안은 유지합니다.", en: "Checking whether the original request was accepted. Your draft is preserved." },
  "composer.uncertain": { ko: "이전 요청이 접수됐을 수 있습니다. 재시작 뒤 자동 재전송하지 않았습니다. 새 입력은 원 요청을 확인한 뒤 보내세요.", en: "Your previous request may have been accepted. It was not automatically resent after reopening. Check it before submitting another request." },
  "composer.invalidPending": { ko: "이전 요청의 복구 정보를 확인하지 못했습니다. 원 작업을 확인하기 전에는 새 key로 제출하지 않습니다.", en: "The saved submission could not be verified. Check the original operation before creating another submission." },
  "composer.accessChecking": { ko: "접근 범위를 다시 확인하는 중입니다. 초안은 계속 쓸 수 있으며 확인이 끝나기 전에는 제출하지 않습니다.", en: "Checking access. You can keep editing your draft; submission is paused until access is confirmed." },
  "composer.savedResearch": { ko: "저장된 연구는 계속 볼 수 있습니다.", en: "Saved research is still available." },
  "composer.chooseConsent": { ko: "동의 고르기", en: "Choose internet consent" },
  "composer.checkModel": { ko: "모델 연결 확인", en: "Check model connection" },
  "composer.needBoth": { ko: "새 연구를 실행하려면 모델 연결과 인터넷 사용 동의를 확인하세요.", en: "Connect a model and choose internet consent to start research." },
  "composer.needConsent": { ko: "새 연구를 실행하려면 인터넷 사용 동의를 고르세요.", en: "Choose internet consent to start research." },
  "composer.needModel": { ko: "새 연구를 실행하려면 모델 연결을 확인하세요.", en: "Check your model connection to start research." },
  "composer.needSetup": { ko: "새 연구를 실행하려면 모델 연결과 초기 설정을 확인하세요.", en: "Check your model connection and initial setup to start research." },
  "composer.corruptDraft": { ko: "이전 초안 저장 기록을 읽지 못했습니다. 기록을 자동 삭제하지 않았으며 새 초안을 저장하면 대체될 수 있습니다.", en: "The saved draft could not be read. It has not been deleted; saving a new draft may replace it." },
  "composer.unavailableDraft": { ko: "이 브라우저의 초안 저장 기록을 읽지 못했습니다. 브라우저 저장소와 작업 공간 식별자를 확인하세요.", en: "The browser draft could not be read. Check browser storage and the workspace identity." },
  "composer.draftScope": { ko: "다른 주소·포트·브라우저 프로필 또는 작업 공간의 초안은 자동 병합되지 않습니다. 서버의 저장 연구는 별도로 읽습니다.", en: "Drafts from other addresses, ports, browser profiles, or workspaces are not merged automatically. Saved research is read separately from the server." },
  "composer.draftTooLarge": { ko: "초안이 브라우저 저장 한도를 넘었습니다. 짧게 줄이기 전에는 재시작 후 복원되지 않을 수 있습니다.", en: "The draft exceeds the browser storage limit. Shorten it to ensure it can be restored after reopening." },
  "composer.draftUnsaved": { ko: "이 브라우저에 초안을 저장하지 못했습니다. 페이지를 닫으면 사라질 수 있습니다.", en: "The draft could not be saved in this browser. It may be lost when the page closes." },
} as const;

export type MessageKey = keyof typeof messages;
