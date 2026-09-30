import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { PreviewApp } from "./PreviewApp";

afterEach(cleanup);

it("labels the preview and disables real login", () => {
  render(<MantineProvider><PreviewApp /></MantineProvider>);
  expect(screen.getByText("UI preview")).toBeVisible();
  expect(screen.getByRole("button", { name: "Continue with Google" })).toBeDisabled();
  expect(document.querySelector('form[action]')).toBeNull();
});

it("allows navigation and settings without enabling discovery", async () => {
  render(<MantineProvider><PreviewApp /></MantineProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Explore the preview" }));
  fireEvent.change(screen.getByRole("textbox", { name: /Search query/ }), { target: { value: "Annual reports" } });
  expect(screen.getByRole("button", { name: "Discover" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Documents" }));
  expect(screen.getByRole("heading", { name: "No stored documents" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Activity" }));
  expect(screen.getByRole("heading", { name: "No processing jobs" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Collect" }));
  expect(screen.getByRole("textbox", { name: /Search query/ })).toHaveValue("Annual reports");
  fireEvent.click(screen.getByRole("button", { name: "Collection settings" }));
  await waitFor(() => expect(screen.getByText("Discovery filters")).toBeVisible());
});
