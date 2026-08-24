import { requestJson } from "../../shared/api-client";
import type { ConversationLearningSettings } from "./types";

const SETTINGS_PATH = "/api/v1/admin/conversation-learning/settings";

export const conversationReviewApi = {
  getLearningSettings: () =>
    requestJson<ConversationLearningSettings>(SETTINGS_PATH),
  updateLearningSettings: (
    current: ConversationLearningSettings,
    enabled: boolean,
  ) => {
    const idempotencyKey = globalThis.crypto.randomUUID();
    return requestJson<ConversationLearningSettings>(SETTINGS_PATH, {
      method: "PATCH",
      body: JSON.stringify({
        enabled,
        expected_settings_revision: current.settings_revision,
        idempotency_key: idempotencyKey,
      }),
    });
  },
};
