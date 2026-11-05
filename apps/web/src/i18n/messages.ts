import { withColumns } from "./withColumns";
import { coreMessages } from "./coreMessages";
import { finishingMessages } from "./finishingMessages";
import { groundingMessages } from "./groundingMessages";
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
import { localRetrievalMessages } from "./localRetrievalMessages";
import { parserErrorMessages } from "./parserErrorMessages";
import { coreZh } from "./zh/coreMessagesZh";
import { coreJa } from "./ja/coreMessagesJa";
import { finishingZh } from "./zh/finishingMessagesZh";
import { finishingJa } from "./ja/finishingMessagesJa";
import { groundingZh } from "./zh/groundingMessagesZh";
import { groundingJa } from "./ja/groundingMessagesJa";
import { remainingZh } from "./zh/remainingMessagesZh";
import { remainingJa } from "./ja/remainingMessagesJa";
import { apiNoticesZh } from "./zh/apiNoticesMessagesZh";
import { apiNoticesJa } from "./ja/apiNoticesMessagesJa";
import { advancedZh } from "./zh/advancedMessagesZh";
import { advancedJa } from "./ja/advancedMessagesJa";
import { reviewPanelsZh } from "./zh/reviewPanelsMessagesZh";
import { reviewPanelsJa } from "./ja/reviewPanelsMessagesJa";
import { activityZh } from "./zh/activityMessagesZh";
import { activityJa } from "./ja/activityMessagesJa";
import { hypothesisActionsZh } from "./zh/hypothesisActionsMessagesZh";
import { hypothesisActionsJa } from "./ja/hypothesisActionsMessagesJa";
import { recordDialogsZh } from "./zh/recordDialogsMessagesZh";
import { recordDialogsJa } from "./ja/recordDialogsMessagesJa";
import { recordPanelsZh } from "./zh/recordPanelsMessagesZh";
import { recordPanelsJa } from "./ja/recordPanelsMessagesJa";
import { researchPanelsZh } from "./zh/researchPanelsMessagesZh";
import { researchPanelsJa } from "./ja/researchPanelsMessagesJa";
import { modelHelpZh } from "./zh/modelHelpMessagesZh";
import { modelHelpJa } from "./ja/modelHelpMessagesJa";
import { historyViewZh } from "./zh/historyViewMessagesZh";
import { historyViewJa } from "./ja/historyViewMessagesJa";
import { judgmentZh } from "./zh/judgmentMessagesZh";
import { judgmentJa } from "./ja/judgmentMessagesJa";
import { followupZh } from "./zh/followupMessagesZh";
import { followupJa } from "./ja/followupMessagesJa";
import { commonStateZh } from "./zh/commonStateMessagesZh";
import { commonStateJa } from "./ja/commonStateMessagesJa";
import { oauthZh } from "./zh/oauthMessagesZh";
import { oauthJa } from "./ja/oauthMessagesJa";
import { sourceZh } from "./zh/sourceMessagesZh";
import { sourceJa } from "./ja/sourceMessagesJa";
import { traceZh } from "./zh/traceMessagesZh";
import { traceJa } from "./ja/traceMessagesJa";
import { researchZh } from "./zh/researchMessagesZh";
import { researchJa } from "./ja/researchMessagesJa";
import { credentialZh } from "./zh/credentialMessagesZh";
import { credentialJa } from "./ja/credentialMessagesJa";
import { workspaceDetailZh } from "./zh/workspaceDetailMessagesZh";
import { workspaceDetailJa } from "./ja/workspaceDetailMessagesJa";
import { workspaceZh } from "./zh/workspaceMessagesZh";
import { workspaceJa } from "./ja/workspaceMessagesJa";
import { setupZh } from "./zh/setupMessagesZh";
import { setupJa } from "./ja/setupMessagesJa";
import { localRetrievalZh } from "./zh/localRetrievalMessagesZh";
import { localRetrievalJa } from "./ja/localRetrievalMessagesJa";
import { parserErrorZh } from "./zh/parserErrorMessagesZh";
import { parserErrorJa } from "./ja/parserErrorMessagesJa";
/** Whole UI messages. Research text and canonical status codes never pass through this catalog. */
export const messages = {
  ...withColumns(coreMessages, { "zh-Hans": coreZh, ja: coreJa }),
  ...withColumns(finishingMessages, { "zh-Hans": finishingZh, ja: finishingJa }),
  ...withColumns(groundingMessages, { "zh-Hans": groundingZh, ja: groundingJa }),
  ...withColumns(remainingMessages, { "zh-Hans": remainingZh, ja: remainingJa }),
  ...withColumns(apiNoticesMessages, { "zh-Hans": apiNoticesZh, ja: apiNoticesJa }),
  ...withColumns(advancedMessages, { "zh-Hans": advancedZh, ja: advancedJa }),
  ...withColumns(reviewPanelsMessages, { "zh-Hans": reviewPanelsZh, ja: reviewPanelsJa }),
  ...withColumns(activityMessages, { "zh-Hans": activityZh, ja: activityJa }),
  ...withColumns(hypothesisActionsMessages, { "zh-Hans": hypothesisActionsZh, ja: hypothesisActionsJa }),
  ...withColumns(recordDialogsMessages, { "zh-Hans": recordDialogsZh, ja: recordDialogsJa }),
  ...withColumns(recordPanelsMessages, { "zh-Hans": recordPanelsZh, ja: recordPanelsJa }),
  ...withColumns(researchPanelsMessages, { "zh-Hans": researchPanelsZh, ja: researchPanelsJa }),
  ...withColumns(modelHelpMessages, { "zh-Hans": modelHelpZh, ja: modelHelpJa }),
  ...withColumns(historyViewMessages, { "zh-Hans": historyViewZh, ja: historyViewJa }),
  ...withColumns(judgmentMessages, { "zh-Hans": judgmentZh, ja: judgmentJa }),
  ...withColumns(followupMessages, { "zh-Hans": followupZh, ja: followupJa }),
  ...withColumns(commonStateMessages, { "zh-Hans": commonStateZh, ja: commonStateJa }),
  ...withColumns(oauthMessages, { "zh-Hans": oauthZh, ja: oauthJa }),
  ...withColumns(sourceMessages, { "zh-Hans": sourceZh, ja: sourceJa }),
  ...withColumns(traceMessages, { "zh-Hans": traceZh, ja: traceJa }),
  ...withColumns(researchMessages, { "zh-Hans": researchZh, ja: researchJa }),
  ...withColumns(credentialMessages, { "zh-Hans": credentialZh, ja: credentialJa }),
  ...withColumns(workspaceDetailMessages, { "zh-Hans": workspaceDetailZh, ja: workspaceDetailJa }),
  ...withColumns(workspaceMessages, { "zh-Hans": workspaceZh, ja: workspaceJa }),
  ...withColumns(setupMessages, { "zh-Hans": setupZh, ja: setupJa }),
  ...withColumns(localRetrievalMessages, { "zh-Hans": localRetrievalZh, ja: localRetrievalJa }),
  ...withColumns(parserErrorMessages, { "zh-Hans": parserErrorZh, ja: parserErrorJa }),
} as const;

export type MessageKey = keyof typeof messages;
