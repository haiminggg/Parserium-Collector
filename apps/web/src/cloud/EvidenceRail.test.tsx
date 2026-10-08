import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { parseriumTheme } from "../theme";
import { EvidenceRail } from "./EvidenceRail";

afterEach(cleanup);

describe("EvidenceRail", () => {
  it("describes the research workflow without implying measured progress", () => {
    render(<MantineProvider theme={parseriumTheme}><EvidenceRail active="documents" /></MantineProvider>);

    expect(screen.getByRole("navigation", { name: "Research workflow" })).toBeVisible();
    expect(screen.getByText("Search")).toBeVisible();
    expect(screen.getByText("Select")).toBeVisible();
    expect(screen.getByText("Parse").closest("li")).toHaveAttribute("data-active", "true");
    expect(screen.getByText("Read").closest("li")).toHaveAttribute("data-active", "true");
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
