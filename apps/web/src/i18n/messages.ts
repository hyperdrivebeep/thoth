import { withZh } from "./withZh";
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
import { finishingZh } from "./zh/finishingMessagesZh";
import { groundingZh } from "./zh/groundingMessagesZh";
import { remainingZh } from "./zh/remainingMessagesZh";
import { apiNoticesZh } from "./zh/apiNoticesMessagesZh";
import { advancedZh } from "./zh/advancedMessagesZh";
import { reviewPanelsZh } from "./zh/reviewPanelsMessagesZh";
import { activityZh } from "./zh/activityMessagesZh";
import { hypothesisActionsZh } from "./zh/hypothesisActionsMessagesZh";
import { recordDialogsZh } from "./zh/recordDialogsMessagesZh";
import { recordPanelsZh } from "./zh/recordPanelsMessagesZh";
import { researchPanelsZh } from "./zh/researchPanelsMessagesZh";
import { modelHelpZh } from "./zh/modelHelpMessagesZh";
import { historyViewZh } from "./zh/historyViewMessagesZh";
import { judgmentZh } from "./zh/judgmentMessagesZh";
import { followupZh } from "./zh/followupMessagesZh";
import { commonStateZh } from "./zh/commonStateMessagesZh";
import { oauthZh } from "./zh/oauthMessagesZh";
import { sourceZh } from "./zh/sourceMessagesZh";
import { traceZh } from "./zh/traceMessagesZh";
import { researchZh } from "./zh/researchMessagesZh";
import { credentialZh } from "./zh/credentialMessagesZh";
import { workspaceDetailZh } from "./zh/workspaceDetailMessagesZh";
import { workspaceZh } from "./zh/workspaceMessagesZh";
import { setupZh } from "./zh/setupMessagesZh";
import { localRetrievalZh } from "./zh/localRetrievalMessagesZh";
import { parserErrorZh } from "./zh/parserErrorMessagesZh";
/** Whole UI messages. Research text and canonical status codes never pass through this catalog. */
export const messages = {
  ...withZh(coreMessages, coreZh),
  ...withZh(finishingMessages, finishingZh),
  ...withZh(groundingMessages, groundingZh),
  ...withZh(remainingMessages, remainingZh),
  ...withZh(apiNoticesMessages, apiNoticesZh),
  ...withZh(advancedMessages, advancedZh),
  ...withZh(reviewPanelsMessages, reviewPanelsZh),
  ...withZh(activityMessages, activityZh),
  ...withZh(hypothesisActionsMessages, hypothesisActionsZh),
  ...withZh(recordDialogsMessages, recordDialogsZh),
  ...withZh(recordPanelsMessages, recordPanelsZh),
  ...withZh(researchPanelsMessages, researchPanelsZh),
  ...withZh(modelHelpMessages, modelHelpZh),
  ...withZh(historyViewMessages, historyViewZh),
  ...withZh(judgmentMessages, judgmentZh),
  ...withZh(followupMessages, followupZh),
  ...withZh(commonStateMessages, commonStateZh),
  ...withZh(oauthMessages, oauthZh),
  ...withZh(sourceMessages, sourceZh),
  ...withZh(traceMessages, traceZh),
  ...withZh(researchMessages, researchZh),
  ...withZh(credentialMessages, credentialZh),
  ...withZh(workspaceDetailMessages, workspaceDetailZh),
  ...withZh(workspaceMessages, workspaceZh),
  ...withZh(setupMessages, setupZh),
  ...withZh(localRetrievalMessages, localRetrievalZh),
  ...withZh(parserErrorMessages, parserErrorZh),
} as const;

export type MessageKey = keyof typeof messages;
