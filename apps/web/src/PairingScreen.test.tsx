import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PairingScreen } from "./PairingScreen";

afterEach(cleanup);

function renderPairing(onPair: (code: string) => Promise<void>) {
  return render(
    <MantineProvider>
      <PairingScreen onPair={onPair} />
    </MantineProvider>,
  );
}

describe("PairingScreen", () => {
  it("submits the operator code without browser persistence", async () => {
    const onPair = vi.fn().mockResolvedValue(undefined);
    const storage = vi.spyOn(Storage.prototype, "setItem");
    renderPairing(onPair);

    expect(screen.getByRole("region", { name: "Browser pairing" })).toBeVisible();

    fireEvent.change(screen.getByLabelText(/Pairing code/), {
      target: { value: "operator-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair browser" }));

    await waitFor(() => expect(onPair).toHaveBeenCalledWith("operator-code"));
    expect(storage).not.toHaveBeenCalled();
    storage.mockRestore();
  });

  it("shows a generic pairing failure", async () => {
    const onPair = vi.fn().mockRejectedValue(new Error("server detail"));
    renderPairing(onPair);

    fireEvent.change(screen.getByLabelText(/Pairing code/), {
      target: { value: "wrong-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair browser" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The pairing code is invalid or expired.",
    );
    expect(screen.queryByText("server detail")).not.toBeInTheDocument();
  });
});
