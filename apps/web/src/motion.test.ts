import { describe, expect, it } from "vitest";

import {
  detailVariants,
  fastTransition,
  rowVariants,
  standardTransition,
  transitionFor,
} from "./motion";

describe("dashboard motion primitives", () => {
  it("keeps operational transitions short and restrained", () => {
    expect(fastTransition.duration).toBe(0.16);
    expect(standardTransition.duration).toBe(0.2);
    expect(rowVariants.hidden).toEqual({ opacity: 0, y: 8 });
    expect(rowVariants.visible).toEqual({ opacity: 1, y: 0 });
    expect(rowVariants.exit).toEqual({ opacity: 0, y: -4 });
    expect(detailVariants.hidden).toMatchObject({ opacity: 0 });
    expect(detailVariants.visible).toMatchObject({ opacity: 1 });
  });

  it("makes nonessential transitions immediate when motion is reduced", () => {
    expect(transitionFor(true)).toEqual({ duration: 0 });
    expect(transitionFor(false)).toBe(standardTransition);
  });
});
