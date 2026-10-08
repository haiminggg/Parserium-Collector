import { describe, expect, it } from "vitest";

import { parseriumTheme } from "./theme";

describe("parseriumTheme", () => {
  it("uses the approved ember accent and compact geometry", () => {
    expect(parseriumTheme.primaryColor).toBe("parseriumEmber");
    expect(parseriumTheme.primaryShade).toEqual({ light: 7, dark: 7 });
    expect(parseriumTheme.defaultRadius).toBe("xs");
    expect(parseriumTheme.colors?.parseriumEmber?.[7]).toBe("#BF380A");
    expect(parseriumTheme.colors?.parseriumGreen?.[6]).toBe("#26783C");
    expect(parseriumTheme.colors?.parseriumRed?.[6]).toBe("#B7352A");
  });
});
