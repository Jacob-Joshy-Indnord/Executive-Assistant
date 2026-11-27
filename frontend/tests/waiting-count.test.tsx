/** Story 6.2: the count for Flow's top bar (task--SyIaIqmydk). */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WaitingCount, waitingCount } from "@/components/assistant/WaitingCount";
import { fakeApi } from "./fakeApi";

describe("WaitingCount", () => {
  it("gives the number of things waiting for the manager", async () => {
    const api = fakeApi({ "GET waiting-count": { count: 3, approvals: 2, attention: 1 } });
    render(<WaitingCount client={api.client} href="/assistant" />);
    const link = await screen.findByRole("link", { name: "3 things wait for you" });
    expect(link).toHaveAttribute("href", "/assistant");
    expect(link).toHaveTextContent("3");
    expect(await waitingCount(api.client)).toBe(3);
  });

  it("shows nothing when nothing waits, or for someone with no assistant", async () => {
    const none = fakeApi({ "GET waiting-count": { count: 0, approvals: 0, attention: 0 } });
    const { container, unmount } = render(<WaitingCount client={none.client} />);
    await waitFor(() => expect(none.calls).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
    unmount();

    const owner = fakeApi({});
    const other = render(<WaitingCount client={owner.client} />);
    await waitFor(() => expect(owner.calls).toHaveLength(1));
    expect(other.container).toBeEmptyDOMElement();
    expect(await waitingCount(owner.client)).toBe(0);
  });
});
