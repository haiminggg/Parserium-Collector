import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DocumentMotion } from "./DocumentMotion";

afterEach(cleanup);

it("plays an explicitly illustrative animation without submitting the search form", async () => {
  let submitted = false;
  const { unmount } = render(<form onSubmit={event => { event.preventDefault(); submitted = true; }}><DocumentMotion /></form>);
  expect(screen.getByRole("heading", { name: "How Parserium works" })).toBeVisible();
  expect(screen.getByText("Illustration only. No live processing.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Play demo" }));
  expect(submitted).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "Replay demo" })).toBeEnabled(), { timeout: 6000 });
  fireEvent.click(screen.getByRole("button", { name: "Replay demo" }));
  unmount();
}, 10000);
