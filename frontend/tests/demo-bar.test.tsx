/** Story 7.1: the demonstration bar. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { personFetch, type BarClient, type BarState } from "@/standins/client";
import { DemoShell } from "@/standins/DemoShell";
import { PERSON_HEADER, currentPerson, resetPerson } from "@/standins/person";

const OPS = "ops-id";
const LEGAL = "legal-id";
const SALES = "sales-id";

function barState(overrides: Partial<BarState> = {}): BarState {
  return {
    now: "2026-11-23T08:00:00+00:00",
    people: [
      { id: OPS, name: "Operations director", role: "Manager" },
      { id: LEGAL, name: "Legal lead", role: "Owner" },
      { id: SALES, name: "Sales director", role: "Another manager" },
    ],
    assistants: [{ flow_id: "flow-ops", manager_id: OPS, manager_name: "Operations director", state: "new" }],
    all_paused: false,
    faults: { assistant_faults: false, assistant_refusing: false, approval: false, notices: false, records: false },
    notices: [],
    assignments: [{ id: "a-1", title: "Fix the vendor list export", state: "blocked" }],
    carried_out: 0,
    records: 0,
    ...overrides,
  };
}

function fakeBar(state: BarState = barState()) {
  const answer = (next: Partial<BarState> = {}) =>
    vi.fn<(...args: unknown[]) => Promise<BarState>>(async () => ({ ...state, ...next }));
  return {
    state: answer(),
    moveClock: answer({ now: "2026-11-23T09:00:00+00:00" }),
    setFault: answer({ faults: { ...state.faults, notices: true } }),
    reset: answer(),
    setUp: answer(),
    handOver: answer(),
    end: answer(),
    pauseAll: answer({ all_paused: true }),
    changeAssignment: answer(),
  } satisfies BarClient;
}

/** A page that counts how often it was opened and shows who it was opened for. */
let opened = 0;
function Page() {
  useEffect(() => {
    opened += 1;
  }, []);
  return <p data-testid="page">Page for {currentPerson()}</p>;
}

async function renderShell(bar = fakeBar()) {
  const user = userEvent.setup();
  render(
    <DemoShell bar={bar}>
      <Page />
    </DemoShell>,
  );
  await screen.findByTestId("page");
  return { bar, user, region: screen.getByRole("region", { name: "Demonstration" }) };
}

beforeEach(() => {
  resetPerson();
  opened = 0;
});

afterEach(() => resetPerson());

describe("Story 7.1 - person switching", () => {
  it("starts as the manager and shows the page only once a person is named", async () => {
    await renderShell();
    expect(screen.getByTestId("page")).toHaveTextContent(`Page for ${OPS}`);
    expect(screen.getByLabelText("Person")).toHaveValue(OPS);
  });

  it("opens the page again for the person chosen in the bar", async () => {
    const { user } = await renderShell();
    await user.selectOptions(screen.getByLabelText("Person"), LEGAL);
    expect(screen.getByTestId("page")).toHaveTextContent(`Page for ${LEGAL}`);
    expect(opened).toBe(2);
  });

  it("names the chosen person with every call to the module", async () => {
    await renderShell();
    const seen: Headers[] = [];
    const fetch = personFetch(async (_input, init) => {
      seen.push(new Headers(init?.headers));
      return new Response("{}");
    });
    await fetch("/api/assistant/assistant", { headers: { Accept: "application/json" } });
    expect(seen[0]?.get(PERSON_HEADER)).toBe(OPS);
    expect(seen[0]?.get("Accept")).toBe("application/json");
  });
});

describe("Story 7.1 - clock, faults and reset", () => {
  it("moves the clock and the page follows", async () => {
    const { bar, user, region } = await renderShell();
    await user.click(within(region).getByRole("button", { name: "Add an hour" }));
    expect(bar.moveClock).toHaveBeenCalledWith("hour");
    await waitFor(() => expect(opened).toBe(2));
    await user.click(within(region).getByRole("button", { name: "Add a day" }));
    await user.click(within(region).getByRole("button", { name: "Go to next step" }));
    expect(bar.moveClock.mock.calls.map((c) => c[0])).toEqual(["hour", "day", "next"]);
  });

  it("turns a fault on and the page follows", async () => {
    const { bar, user, region } = await renderShell();
    await user.click(within(region).getByRole("button", { name: "Show controls" }));
    await user.click(within(region).getByRole("switch", { name: /Notices cannot be sent/ }));
    expect(bar.setFault).toHaveBeenCalledWith("notices", true);
    await waitFor(() => expect(opened).toBe(2));
  });

  it("starts again from the sample data", async () => {
    const { bar, user, region } = await renderShell();
    await user.click(within(region).getByRole("button", { name: "Show controls" }));
    await user.click(within(region).getByRole("button", { name: "Start again" }));
    expect(bar.reset).toHaveBeenCalled();
    await waitFor(() => expect(opened).toBe(2));
  });

  it("shows a failure as one sentence and keeps the page", async () => {
    const bar = fakeBar();
    bar.moveClock.mockRejectedValueOnce(new Error("boom"));
    const { user, region } = await renderShell(bar);
    await user.click(within(region).getByRole("button", { name: "Add an hour" }));
    expect(await within(region).findByText("Something went wrong. Try again.")).toBeInTheDocument();
    expect(opened).toBe(1);
  });
});

describe("Story 7.1 - Flow calls simulated", () => {
  it("sets up, hands over, ends and pauses everything through the bar", async () => {
    const { bar, user, region } = await renderShell();
    await user.click(within(region).getByRole("button", { name: "Show controls" }));

    await user.selectOptions(within(region).getByLabelText("Set up an assistant for"), SALES);
    await user.click(within(region).getByRole("button", { name: "Set up" }));
    expect(bar.setUp).toHaveBeenCalledWith(SALES);

    await user.selectOptions(within(region).getByLabelText("To"), SALES);
    await user.click(within(region).getByRole("button", { name: "Hand over" }));
    expect(bar.handOver).toHaveBeenCalledWith("flow-ops", SALES);

    await user.click(within(region).getByRole("button", { name: "End" }));
    expect(bar.end).toHaveBeenCalledWith("flow-ops");

    await user.click(within(region).getByRole("button", { name: "Pause everything" }));
    expect(bar.pauseAll).toHaveBeenCalledWith(true);
    expect(await within(region).findByRole("button", { name: "Resume everything" })).toBeInTheDocument();
  });

  it("changes an assignment in the work data", async () => {
    const { bar, user, region } = await renderShell();
    await user.click(within(region).getByRole("button", { name: "Show controls" }));
    await user.selectOptions(within(region).getByLabelText("New state"), "in_progress");
    await user.click(within(region).getByRole("button", { name: "Change state" }));
    expect(bar.changeAssignment).toHaveBeenCalledWith("a-1", "in_progress");
  });

  it("lists the notices the stand-ins sent", async () => {
    const bar = fakeBar(barState({ notices: [{ to: "Legal lead", text: "You have a new action item." }] }));
    const { user, region } = await renderShell(bar);
    expect(within(region).getByText("1 notice sent")).toBeInTheDocument();
    await user.click(within(region).getByRole("button", { name: "Show controls" }));
    const list = within(region).getByRole("list", { name: "Notices sent" });
    expect(list).toHaveTextContent("To Legal lead: You have a new action item.");
  });
});

describe("Story 7.1 - the bar is not part of the module", () => {
  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : [];
    });
  }

  it.each([...files("components"), ...files("lib")])("%s does not use the demonstration", (file) => {
    const source = readFileSync(file, "utf8");
    expect(source).not.toMatch(/from\s+["'](?:@\/|\.\.\/)*standins/);
    expect(source).not.toMatch(/X-Demo-Person|\/api\/demo/i);
  });

  it("is shown only by the demonstration's own layout", () => {
    const users = [...files("app"), ...files("components"), ...files("lib")].filter((file) =>
      /from\s+["']@\/standins/.test(readFileSync(file, "utf8")),
    );
    expect(users.every((file) => file.startsWith("app"))).toBe(true);
  });
});

it("tells the time in words", async () => {
  await renderShell();
  await act(async () => undefined);
  expect(screen.getByText(/Time:/)).toHaveTextContent(/Monday 23 November/);
});
