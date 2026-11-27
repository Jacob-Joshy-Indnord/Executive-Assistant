/** Story 6.1: a visible page refreshes every 30 seconds without moving what the person is doing. */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { REFRESH_MS, useResource } from "@/lib/assistant/refresh";
import { Input } from "@/components/assistant/parts";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

function Page({ load }: { load: () => Promise<string[]> }) {
  const { data } = useResource(load);
  const [draft, setDraft] = useState("");
  return (
    <div>
      <ul>
        {data?.map((row) => (
          <li key={row}>{row}</li>
        ))}
      </ul>
      <label htmlFor="reply">Reply</label>
      <Input id="reply" value={draft} onChange={(e) => setDraft(e.target.value)} />
    </div>
  );
}

const flush = () => act(async () => {
  await Promise.resolve();
});

describe("useResource", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reloads every 30 seconds while visible, keeping what was typed and the focus", async () => {
    let rows = ["Finish the review"];
    const load = vi.fn(() => Promise.resolve(rows));
    render(<Page load={load} />);
    await flush();
    expect(screen.getByText("Finish the review")).toBeInTheDocument();

    const field = screen.getByLabelText("Reply") as HTMLInputElement;
    field.focus();
    fireEvent.change(field, { target: { value: "Half written" } });

    rows = ["Finish the review", "Send the figures"];
    await act(async () => {
      vi.advanceTimersByTime(REFRESH_MS);
    });
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Send the figures")).toBeInTheDocument();
    expect(field).toHaveValue("Half written");
    expect(field).toHaveFocus();
  });

  it("does not reload while the page is hidden, and catches up when it shows again", async () => {
    const load = vi.fn(() => Promise.resolve(["a"]));
    render(<Page load={load} />);
    await flush();
    act(() => setVisibility("hidden"));
    await act(async () => {
      vi.advanceTimersByTime(REFRESH_MS * 3);
    });
    expect(load).toHaveBeenCalledTimes(1);
    act(() => setVisibility("visible"));
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps what is shown when a refresh fails", async () => {
    let fail = false;
    const load = vi.fn(() => (fail ? Promise.reject(new Error("down")) : Promise.resolve(["kept"])));
    render(<Page load={load} />);
    await flush();
    fail = true;
    await act(async () => {
      vi.advanceTimersByTime(REFRESH_MS);
    });
    await flush();
    expect(screen.getByText("kept")).toBeInTheDocument();
  });
});
