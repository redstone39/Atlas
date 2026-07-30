import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";

import { en } from "../locales/en";
import { zhTW } from "../locales/zh-TW";
import { localizeMessage } from "./user-messages";

async function translator(language: "en" | "zh-TW") {
  const instance = createInstance();
  await instance.init({
    resources: { en: { translation: en }, "zh-TW": { translation: zhTW } },
    lng: language,
    fallbackLng: "en",
  });
  return instance.t;
}

describe("localized user-message contract", () => {
  it("renders a stable code in the selected language", async () => {
    expect(localizeMessage("result.answered_from_validated_evidence", await translator("zh-TW"))).toBe(
      "已使用你可存取的已驗證證據回答。",
    );
  });

  it("fails closed to localized generic copy for prose, unknown codes, or invalid params", async () => {
    const t = await translator("en");
    const unsafeValues = [
      "Raw backend failure detail",
      "unknown.message_code",
      { message_code: "result.answered_from_validated_evidence", message_params: { extra: true } },
      { message_code: "result.answered_from_validated_evidence", message_params: [] },
    ];
    for (const value of unsafeValues) {
      expect(localizeMessage(value, t)).toBe(
        "The request could not be completed. Please try again.",
      );
    }
  });
});
