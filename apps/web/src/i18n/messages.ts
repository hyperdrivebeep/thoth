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
import { coreZhHant } from "./zhHant/coreMessagesZhHant";
import { coreJa } from "./ja/coreMessagesJa";
import { finishingZh } from "./zh/finishingMessagesZh";
import { finishingZhHant } from "./zhHant/finishingMessagesZhHant";
import { finishingJa } from "./ja/finishingMessagesJa";
import { groundingZh } from "./zh/groundingMessagesZh";
import { groundingZhHant } from "./zhHant/groundingMessagesZhHant";
import { groundingJa } from "./ja/groundingMessagesJa";
import { remainingZh } from "./zh/remainingMessagesZh";
import { remainingZhHant } from "./zhHant/remainingMessagesZhHant";
import { remainingJa } from "./ja/remainingMessagesJa";
import { apiNoticesZh } from "./zh/apiNoticesMessagesZh";
import { apiNoticesZhHant } from "./zhHant/apiNoticesMessagesZhHant";
import { apiNoticesJa } from "./ja/apiNoticesMessagesJa";
import { advancedZh } from "./zh/advancedMessagesZh";
import { advancedZhHant } from "./zhHant/advancedMessagesZhHant";
import { advancedJa } from "./ja/advancedMessagesJa";
import { reviewPanelsZh } from "./zh/reviewPanelsMessagesZh";
import { reviewPanelsZhHant } from "./zhHant/reviewPanelsMessagesZhHant";
import { reviewPanelsJa } from "./ja/reviewPanelsMessagesJa";
import { activityZh } from "./zh/activityMessagesZh";
import { activityZhHant } from "./zhHant/activityMessagesZhHant";
import { activityJa } from "./ja/activityMessagesJa";
import { hypothesisActionsZh } from "./zh/hypothesisActionsMessagesZh";
import { hypothesisActionsZhHant } from "./zhHant/hypothesisActionsMessagesZhHant";
import { hypothesisActionsJa } from "./ja/hypothesisActionsMessagesJa";
import { recordDialogsZh } from "./zh/recordDialogsMessagesZh";
import { recordDialogsZhHant } from "./zhHant/recordDialogsMessagesZhHant";
import { recordDialogsJa } from "./ja/recordDialogsMessagesJa";
import { recordPanelsZh } from "./zh/recordPanelsMessagesZh";
import { recordPanelsZhHant } from "./zhHant/recordPanelsMessagesZhHant";
import { recordPanelsJa } from "./ja/recordPanelsMessagesJa";
import { researchPanelsZh } from "./zh/researchPanelsMessagesZh";
import { researchPanelsZhHant } from "./zhHant/researchPanelsMessagesZhHant";
import { researchPanelsJa } from "./ja/researchPanelsMessagesJa";
import { modelHelpZh } from "./zh/modelHelpMessagesZh";
import { modelHelpZhHant } from "./zhHant/modelHelpMessagesZhHant";
import { modelHelpJa } from "./ja/modelHelpMessagesJa";
import { historyViewZh } from "./zh/historyViewMessagesZh";
import { historyViewZhHant } from "./zhHant/historyViewMessagesZhHant";
import { historyViewJa } from "./ja/historyViewMessagesJa";
import { judgmentZh } from "./zh/judgmentMessagesZh";
import { judgmentZhHant } from "./zhHant/judgmentMessagesZhHant";
import { judgmentJa } from "./ja/judgmentMessagesJa";
import { followupZh } from "./zh/followupMessagesZh";
import { followupZhHant } from "./zhHant/followupMessagesZhHant";
import { followupJa } from "./ja/followupMessagesJa";
import { commonStateZh } from "./zh/commonStateMessagesZh";
import { commonStateZhHant } from "./zhHant/commonStateMessagesZhHant";
import { commonStateJa } from "./ja/commonStateMessagesJa";
import { oauthZh } from "./zh/oauthMessagesZh";
import { oauthZhHant } from "./zhHant/oauthMessagesZhHant";
import { oauthJa } from "./ja/oauthMessagesJa";
import { sourceZh } from "./zh/sourceMessagesZh";
import { sourceZhHant } from "./zhHant/sourceMessagesZhHant";
import { sourceJa } from "./ja/sourceMessagesJa";
import { traceZh } from "./zh/traceMessagesZh";
import { traceZhHant } from "./zhHant/traceMessagesZhHant";
import { traceJa } from "./ja/traceMessagesJa";
import { researchZh } from "./zh/researchMessagesZh";
import { researchZhHant } from "./zhHant/researchMessagesZhHant";
import { researchJa } from "./ja/researchMessagesJa";
import { credentialZh } from "./zh/credentialMessagesZh";
import { credentialZhHant } from "./zhHant/credentialMessagesZhHant";
import { credentialJa } from "./ja/credentialMessagesJa";
import { workspaceDetailZh } from "./zh/workspaceDetailMessagesZh";
import { workspaceDetailZhHant } from "./zhHant/workspaceDetailMessagesZhHant";
import { workspaceDetailJa } from "./ja/workspaceDetailMessagesJa";
import { workspaceZh } from "./zh/workspaceMessagesZh";
import { workspaceZhHant } from "./zhHant/workspaceMessagesZhHant";
import { workspaceJa } from "./ja/workspaceMessagesJa";
import { setupZh } from "./zh/setupMessagesZh";
import { setupZhHant } from "./zhHant/setupMessagesZhHant";
import { setupJa } from "./ja/setupMessagesJa";
import { localRetrievalZh } from "./zh/localRetrievalMessagesZh";
import { localRetrievalZhHant } from "./zhHant/localRetrievalMessagesZhHant";
import { localRetrievalJa } from "./ja/localRetrievalMessagesJa";
import { parserErrorZh } from "./zh/parserErrorMessagesZh";
import { parserErrorZhHant } from "./zhHant/parserErrorMessagesZhHant";
import { parserErrorJa } from "./ja/parserErrorMessagesJa";
/** Whole UI messages. Research text and canonical status codes never pass through this catalog. */
export const messages = {
  ...withColumns(coreMessages, { "zh-Hans": coreZh, "zh-Hant": coreZhHant, ja: coreJa }),
  ...withColumns(finishingMessages, {
    "zh-Hans": finishingZh,
    "zh-Hant": finishingZhHant,
    ja: finishingJa,
  }),
  ...withColumns(groundingMessages, {
    "zh-Hans": groundingZh,
    "zh-Hant": groundingZhHant,
    ja: groundingJa,
  }),
  ...withColumns(remainingMessages, {
    "zh-Hans": remainingZh,
    "zh-Hant": remainingZhHant,
    ja: remainingJa,
  }),
  ...withColumns(apiNoticesMessages, {
    "zh-Hans": apiNoticesZh,
    "zh-Hant": apiNoticesZhHant,
    ja: apiNoticesJa,
  }),
  ...withColumns(advancedMessages, {
    "zh-Hans": advancedZh,
    "zh-Hant": advancedZhHant,
    ja: advancedJa,
  }),
  ...withColumns(reviewPanelsMessages, {
    "zh-Hans": reviewPanelsZh,
    "zh-Hant": reviewPanelsZhHant,
    ja: reviewPanelsJa,
  }),
  ...withColumns(activityMessages, {
    "zh-Hans": activityZh,
    "zh-Hant": activityZhHant,
    ja: activityJa,
  }),
  ...withColumns(hypothesisActionsMessages, {
    "zh-Hans": hypothesisActionsZh,
    "zh-Hant": hypothesisActionsZhHant,
    ja: hypothesisActionsJa,
  }),
  ...withColumns(recordDialogsMessages, {
    "zh-Hans": recordDialogsZh,
    "zh-Hant": recordDialogsZhHant,
    ja: recordDialogsJa,
  }),
  ...withColumns(recordPanelsMessages, {
    "zh-Hans": recordPanelsZh,
    "zh-Hant": recordPanelsZhHant,
    ja: recordPanelsJa,
  }),
  ...withColumns(researchPanelsMessages, {
    "zh-Hans": researchPanelsZh,
    "zh-Hant": researchPanelsZhHant,
    ja: researchPanelsJa,
  }),
  ...withColumns(modelHelpMessages, {
    "zh-Hans": modelHelpZh,
    "zh-Hant": modelHelpZhHant,
    ja: modelHelpJa,
  }),
  ...withColumns(historyViewMessages, {
    "zh-Hans": historyViewZh,
    "zh-Hant": historyViewZhHant,
    ja: historyViewJa,
  }),
  ...withColumns(judgmentMessages, {
    "zh-Hans": judgmentZh,
    "zh-Hant": judgmentZhHant,
    ja: judgmentJa,
  }),
  ...withColumns(followupMessages, {
    "zh-Hans": followupZh,
    "zh-Hant": followupZhHant,
    ja: followupJa,
  }),
  ...withColumns(commonStateMessages, {
    "zh-Hans": commonStateZh,
    "zh-Hant": commonStateZhHant,
    ja: commonStateJa,
  }),
  ...withColumns(oauthMessages, { "zh-Hans": oauthZh, "zh-Hant": oauthZhHant, ja: oauthJa }),
  ...withColumns(sourceMessages, { "zh-Hans": sourceZh, "zh-Hant": sourceZhHant, ja: sourceJa }),
  ...withColumns(traceMessages, { "zh-Hans": traceZh, "zh-Hant": traceZhHant, ja: traceJa }),
  ...withColumns(researchMessages, {
    "zh-Hans": researchZh,
    "zh-Hant": researchZhHant,
    ja: researchJa,
  }),
  ...withColumns(credentialMessages, {
    "zh-Hans": credentialZh,
    "zh-Hant": credentialZhHant,
    ja: credentialJa,
  }),
  ...withColumns(workspaceDetailMessages, {
    "zh-Hans": workspaceDetailZh,
    "zh-Hant": workspaceDetailZhHant,
    ja: workspaceDetailJa,
  }),
  ...withColumns(workspaceMessages, {
    "zh-Hans": workspaceZh,
    "zh-Hant": workspaceZhHant,
    ja: workspaceJa,
  }),
  ...withColumns(setupMessages, { "zh-Hans": setupZh, "zh-Hant": setupZhHant, ja: setupJa }),
  ...withColumns(localRetrievalMessages, {
    "zh-Hans": localRetrievalZh,
    "zh-Hant": localRetrievalZhHant,
    ja: localRetrievalJa,
  }),
  ...withColumns(parserErrorMessages, {
    "zh-Hans": parserErrorZh,
    "zh-Hant": parserErrorZhHant,
    ja: parserErrorJa,
  }),
} as const;

export type MessageKey = keyof typeof messages;
