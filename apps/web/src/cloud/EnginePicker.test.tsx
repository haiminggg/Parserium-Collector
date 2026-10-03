import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ParseEngine } from "./api";
import { EnginePicker, engineLabel } from "./EnginePicker";

afterEach(cleanup);

const engines: ParseEngine[] = [
  { id: "liteparse", label: "LiteParse", ocr: true, license: "Apache-2.0", description: "Layout-aware extraction." },
  { id: "markitdown", label: "MarkItDown", ocr: false, license: "MIT", description: "Fast extraction, no OCR." },
];

describe("EnginePicker", () => {
  it("lists every engine and describes the selected one", () => {
    render(<EnginePicker engines={engines} value="markitdown" onChange={vi.fn()} />);

    expect(screen.getByLabelText("Parser")).toHaveValue("markitdown");
    expect(screen.getAllByRole("option").map(option => option.textContent)).toEqual(["LiteParse", "MarkItDown"]);
    expect(screen.getByText("Fast extraction, no OCR.")).toBeVisible();
    expect(screen.getByText("No OCR")).toBeVisible();
    expect(screen.getByText("MIT")).toBeVisible();
  });

  it("reports the engine the user picks", () => {
    const onChange = vi.fn();
    render(<EnginePicker engines={engines} value="liteparse" onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Parser"), { target: { value: "markitdown" } });

    expect(onChange).toHaveBeenCalledWith("markitdown");
  });

  it("falls back to the first engine when the saved choice is unknown", () => {
    render(<EnginePicker engines={engines} value="gone" onChange={vi.fn()} />);

    expect(screen.getByLabelText("Parser")).toHaveValue("liteparse");
    expect(screen.getByText("Reads scanned pages (OCR)")).toBeVisible();
  });

  it("renders nothing without engines and disables while busy", () => {
    const { container, rerender } = render(<EnginePicker engines={[]} value="" onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<EnginePicker engines={engines} value="liteparse" disabled onChange={vi.fn()} />);
    expect(screen.getByLabelText("Parser")).toBeDisabled();
  });

  it("labels engines by id and keeps unknown ids readable", () => {
    expect(engineLabel(engines, "markitdown")).toBe("MarkItDown");
    expect(engineLabel(engines, "retired")).toBe("retired");
    expect(engineLabel(undefined, "markitdown")).toBe("markitdown");
    expect(engineLabel(engines, null)).toBeNull();
  });
});
