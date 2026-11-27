/**
 * Story 7.2 - It works end to end: one run through the demonstration, driven through the bar.
 *
 * The clock starts on Monday 23 November 2026 at 08:00 (UTC, the browser's time zone) and is
 * only ever moved through the bar. With the section 8.1 defaults, reports are on Mondays at
 * 08:00, owners are asked on the Thursday before at 09:00, and drafts are made on the
 * Wednesday at 09:00. "Awaiting confirmation" needs an earlier confirmation, so the run goes
 * through two reports: in the first every owner gives a status; in the second the Programme
 * lead confirms, the Legal lead corrects the assistant's draft and the Finance lead does not
 * answer.
 */
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

const START = new Date("2026-11-23T08:00:00Z");

const MANAGER = "Operations director (Manager)";
const LEGAL = "Legal lead (Owner)";
const FINANCE = "Finance lead (Owner)";
const PROGRAMME = "Programme lead (Owner)";
const OTHER_MANAGER = "Sales director (Another manager)";
const ADMINISTRATOR = "Organisation administrator (Administrator)";

const ITEM = "Finish the contract review";
const NO_ASSISTANT = "You have no Executive Assistant yet.";

/** The scripted assistant's draft wording (backend/standins/assistant.py): never confirmed. */
const DRAFT_WORDING = ["Drafted from the news since the last report", "Confirm the next step with the owner"];

/** The Legal lead's own words when correcting the draft. */
const CORRECTED = {
  reason: "Two vendor contracts are still open.",
  whatChanged: "The vendor list export is moving again.",
  whatNext: "Sign the last two contracts by Friday.",
};

type BarState = {
  now: string;
  people: { id: string; name: string }[];
  notices: { to: string; text: string }[];
  carried_out: number;
};

// --- The bar -------------------------------------------------------------------------------

const bar = (page: Page) => page.getByRole("region", { name: "Demonstration" });

async function barState(request: APIRequestContext): Promise<BarState> {
  const response = await request.get("/api/demo");
  expect(response.ok()).toBe(true);
  return (await response.json()) as BarState;
}

async function personId(request: APIRequestContext, name: string): Promise<string> {
  const found = (await barState(request)).people.find((p) => p.name === name);
  expect(found, `${name} is in the sample organisation`).toBeTruthy();
  return found!.id;
}

/** Choose the person in the bar, then open the page as they see it. */
async function openAs(page: Page, person: string, path: string) {
  await bar(page).getByLabel("Person", { exact: true }).selectOption({ label: person });
  if (new URL(page.url()).pathname !== path) await page.goto(path);
  await expect(bar(page).getByLabel("Person", { exact: true })).toHaveValue(/.+/);
}

async function moveClock(page: Page, label: "Add an hour" | "Add a day", times = 1) {
  const time = bar(page).locator("time");
  for (let i = 0; i < times; i++) {
    const before = await time.getAttribute("datetime");
    await bar(page).getByRole("button", { name: label }).click();
    await expect(time).not.toHaveAttribute("datetime", before ?? "");
  }
}

async function expectTime(page: Page, iso: string) {
  await expect(bar(page).locator("time")).toHaveAttribute("datetime", iso);
}

async function controls(page: Page): Promise<Locator> {
  const show = bar(page).getByRole("button", { name: "Show controls" });
  if (await show.isVisible()) await show.click();
  return bar(page);
}

async function hideControls(page: Page) {
  const hide = bar(page).getByRole("button", { name: "Hide controls" });
  if (await hide.isVisible()) await hide.click();
}

/** What the stand-ins have sent so far: notices delivered and actions Flow carried out. */
async function sent(request: APIRequestContext) {
  const state = await barState(request);
  return { notices: state.notices.length, carriedOut: state.carried_out };
}

// --- The module's addresses, as one person ----------------------------------------------------

async function apiAs(request: APIRequestContext, person: string, path: string) {
  return request.get(`/api/assistant/${path}`, { headers: { "X-Demo-Person": person } });
}

// --- The pages -----------------------------------------------------------------------------

const section = (page: Page, title: string) => page.getByRole("region", { name: title, exact: true });

async function openSection(page: Page, title: string) {
  const open = section(page, title).getByRole("button", { name: `Open ${title}`, exact: true });
  if (await open.isVisible()) await open.click();
}

const askedOfYou = (page: Page) => page.getByRole("list", { name: "Requests for you", exact: true });

async function giveStatus(page: Page, project: string, colour: "Green" | "Amber" | "Red", reason: string) {
  const row = askedOfYou(page).getByRole("listitem").filter({ hasText: `status of ${project}` });
  await row.getByRole("radio", { name: new RegExp(`^${colour}`) }).check();
  await row.getByLabel("Reason", { exact: true }).fill(reason);
  await row.getByRole("button", { name: "Give status" }).click();
  await expect(row).toHaveCount(0);
}

async function approveStatusRounds(page: Page, confirmers: number) {
  await openAs(page, MANAGER, "/assistant");
  const approvals = section(page, "Needs your approval");
  await expect(approvals.getByRole("listitem").filter({ hasText: "for the status of" })).toHaveCount(confirmers);
  await approvals.getByRole("button", { name: "Approve all" }).click();
  await expect(approvals.getByText("Nothing waits for your approval.")).toBeVisible();
}

test("Story 7.2 - it works end to end", async ({ page, request }) => {
  // The browser's own date follows the demonstration, not the real day (date fields' minimum).
  await page.clock.setFixedTime(START);
  await page.goto("/assistant");
  await (await controls(page)).getByRole("button", { name: "Start again" }).click();
  await expectTime(page, "2026-11-23T08:00:00+00:00");
  await hideControls(page);

  const ids = {
    manager: await personId(request, "Operations director"),
    legal: await personId(request, "Legal lead"),
    otherManager: await personId(request, "Sales director"),
    administrator: await personId(request, "Organisation administrator"),
  };

  await test.step("Form gates activity, item accepted", async () => {
    await openAs(page, MANAGER, "/assistant");
    await expect(page.getByRole("region", { name: "How you work" })).toBeVisible();

    await moveClock(page, "Add an hour", 2);
    await expectTime(page, "2026-11-23T10:00:00+00:00");
    expect(await sent(request)).toEqual({ notices: 0, carriedOut: 0 });

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(section(page, "Needs your approval")).toBeVisible();

    await openSection(page, "Action items");
    await section(page, "Action items").getByRole("button", { name: "Add an action item" }).click();
    const form = page.getByRole("form", { name: "Add an action item" });
    await form.getByLabel("What is to be done").fill(ITEM);
    await form.getByLabel("Owner", { exact: true }).fill("Legal lead");
    await form.getByLabel("Due date").fill("2026-11-27");
    await form.getByRole("button", { name: "Add item" }).click();
    const items = page.getByRole("list", { name: "Action items", exact: true });
    await expect(items.getByRole("listitem").filter({ hasText: ITEM })).toContainText("Waiting for the owner");

    await openAs(page, LEGAL, "/assistant/requests");
    const ask = askedOfYou(page).getByRole("listitem").filter({ hasText: ITEM });
    await expect(ask).toContainText("Operations director asks you to accept this action item");
    await ask.getByRole("button", { name: "Accept", exact: true }).click();
    await expect(
      page.getByRole("list", { name: "Your action items", exact: true }).getByRole("listitem").filter({ hasText: ITEM }),
    ).toContainText("Open");
  });

  await test.step("Follow three projects", async () => {
    await openAs(page, MANAGER, "/assistant/settings");
    for (const project of ["CRM rollout", "Vendor consolidation", "Hiring plan"]) {
      const row = page.getByRole("list", { name: "Projects", exact: true }).getByRole("listitem").filter({ hasText: project });
      await row.getByRole("button", { name: "Follow", exact: true }).click();
      await row.getByRole("button", { name: "Follow", exact: true }).click();
      await expect(row).toContainText("Followed");
    }
  });

  await test.step("Follow-up to extension", async () => {
    // Half-way between Monday and Friday in working days: Wednesday at 09:00.
    await moveClock(page, "Add a day", 2);
    await expectTime(page, "2026-11-25T10:00:00+00:00");

    await openAs(page, MANAGER, "/assistant");
    const followUp = section(page, "Needs your approval")
      .getByRole("listitem")
      .filter({ hasText: "Ask the Legal lead for an update" });
    await expect(followUp).toHaveCount(1);
    await followUp.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(followUp).toHaveCount(0);

    await openAs(page, LEGAL, "/assistant/requests");
    const ask = askedOfYou(page).getByRole("listitem").filter({ hasText: ITEM });
    await expect(ask).toContainText("asks you for an update");
    await ask.getByRole("button", { name: "Update", exact: true }).click();
    await ask.getByRole("radio", { name: "Blocked" }).check();
    await ask.getByLabel("Note (optional)").fill("Waiting for the supplier's signature.");
    await ask.getByRole("button", { name: "Send update" }).click();
    await expect(ask).toHaveCount(0);

    await openAs(page, MANAGER, "/assistant");
    const entry = page
      .getByRole("list", { name: "Things that need your decision" })
      .getByRole("listitem")
      .filter({ hasText: ITEM });
    await expect(entry).toContainText("Waiting for the supplier's signature.");
    await entry.getByRole("group", { name: `Choices for ${ITEM}` }).getByRole("button", { name: "Extend" }).click();
    await entry.getByLabel("New due date").fill("2026-12-11");
    await entry.getByRole("button", { name: "Extend", exact: true }).click();
    await expect(entry).toHaveCount(0);

    await openSection(page, "Action items");
    await expect(
      page.getByRole("list", { name: "Action items", exact: true }).getByRole("listitem").filter({ hasText: ITEM }),
    ).toContainText("Due Fri 11 Dec");
  });

  await test.step("First report: every owner gives a status", async () => {
    await moveClock(page, "Add a day");
    await expectTime(page, "2026-11-26T10:00:00+00:00");
    await approveStatusRounds(page, 3);

    await openAs(page, PROGRAMME, "/assistant/requests");
    await giveStatus(page, "CRM rollout", "Green", "The data import is on plan.");
    await openAs(page, LEGAL, "/assistant/requests");
    await giveStatus(page, "Vendor consolidation", "Amber", "Three vendor contracts are open.");
    await openAs(page, FINANCE, "/assistant/requests");
    await giveStatus(page, "Hiring plan", "Green", "The hiring forms are being checked.");

    // Monday 30 November, 08:00: the first report.
    await moveClock(page, "Add a day", 4);
    await expectTime(page, "2026-11-30T10:00:00+00:00");

    // News for two projects in Flow's work data, so the assistant drafts their statements.
    const shown = await controls(page);
    await shown.getByLabel("Assignment", { exact: true }).selectOption({ label: "Fix the vendor list export (Blocked)" });
    await shown.getByLabel("New state").selectOption({ label: "In progress" });
    await shown.getByRole("button", { name: "Change state" }).click();
    await expect(shown.getByLabel("Assignment", { exact: true })).toContainText("Fix the vendor list export (In progress)");
    await shown.getByLabel("Assignment", { exact: true }).selectOption({ label: "Check the hiring plan forms (Approved)" });
    await shown.getByLabel("New state").selectOption({ label: "Waiting for review" });
    await shown.getByRole("button", { name: "Change state" }).click();
    await expect(shown.getByLabel("Assignment", { exact: true })).toContainText(
      "Check the hiring plan forms (Waiting for review)",
    );
    await hideControls(page);
  });

  await test.step("Report marks correct", async () => {
    // Drafts on Wednesday 2 December, owners asked on Thursday 3 December.
    await moveClock(page, "Add a day", 3);
    await expectTime(page, "2026-12-03T10:00:00+00:00");
    await approveStatusRounds(page, 3);

    await openAs(page, PROGRAMME, "/assistant/requests");
    const crm = askedOfYou(page).getByRole("listitem").filter({ hasText: "status of CRM rollout" });
    await expect(crm.getByLabel("Reason", { exact: true })).toHaveValue("The data import is on plan.");
    await crm.getByRole("button", { name: "Give status" }).click();
    await expect(crm).toHaveCount(0);

    await openAs(page, LEGAL, "/assistant/requests");
    const vendor = askedOfYou(page).getByRole("listitem").filter({ hasText: "status of Vendor consolidation" });
    await expect(vendor).toContainText("The assistant drafted this.");
    await expect(vendor.getByLabel("Reason", { exact: true })).toHaveValue(/Drafted from the news/);
    await vendor.getByRole("radio", { name: /^Amber/ }).check();
    await vendor.getByLabel("Reason", { exact: true }).fill(CORRECTED.reason);
    await vendor.getByLabel("What changed").fill(CORRECTED.whatChanged);
    await vendor.getByLabel("What comes next").fill(CORRECTED.whatNext);
    await vendor.getByRole("button", { name: "Confirm with changes" }).click();
    await expect(vendor).toHaveCount(0);

    // The Finance lead is asked to confirm a draft too, and does not answer.
    await openAs(page, FINANCE, "/assistant/requests");
    await expect(
      askedOfYou(page).getByRole("listitem").filter({ hasText: "to confirm the status of Hiring plan" }),
    ).toHaveCount(1);

    // Monday 7 December, 08:00: the second report.
    await moveClock(page, "Add a day", 4);
    await expectTime(page, "2026-12-07T10:00:00+00:00");

    await openAs(page, MANAGER, "/assistant");
    await openSection(page, "Project status");
    const reports = page.getByRole("list", { name: "Reports made", exact: true });
    await expect(reports.getByRole("listitem")).toHaveCount(2);
    await reports.getByRole("button", { name: /Report of Mon 7 Dec, 08:00/ }).click();
    const lines = page.getByRole("list", { name: "Report lines", exact: true });
    await expect(lines.getByRole("listitem").filter({ hasText: "CRM rollout" })).toContainText("Confirmed");
    await expect(lines.getByRole("listitem").filter({ hasText: "Vendor consolidation" })).toContainText(
      "Corrected by its owner",
    );
    await expect(lines.getByRole("listitem").filter({ hasText: "Vendor consolidation" })).toContainText(
      CORRECTED.reason,
    );
    await expect(lines.getByRole("listitem").filter({ hasText: "Hiring plan" })).toContainText(
      "Awaiting confirmation",
    );
    await expect(lines.getByRole("listitem").filter({ hasText: "Hiring plan" })).toContainText(
      "The hiring forms are being checked.",
    );

    // The unconfirmed wording is nowhere in what the manager receives: the page with each
    // live line opened in turn (one is open at a time), the module's answers to them, and the
    // notices sent to them.
    const received: string[] = [await page.locator("main").innerText()];
    const followed = page.getByRole("list", { name: "Projects followed", exact: true });
    for (const project of ["CRM rollout", "Hiring plan", "Vendor consolidation"]) {
      await followed.getByRole("button", { name: new RegExp(`^${project}`) }).click();
      await expect(followed.getByRole("button", { name: new RegExp(`^${project}`) })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      received.push(await page.locator("main").innerText());
    }
    await expect(followed).toContainText(CORRECTED.whatNext);
    for (const path of ["status", "reports", "attention", "requests", "done", "chat", "action-items"]) {
      const response = await apiAs(request, ids.manager, path);
      expect(response.ok(), path).toBe(true);
      received.push(await response.text());
    }
    const reportList = (await (await apiAs(request, ids.manager, "reports")).json()) as {
      reports: { id: string; lines: { statement_id?: string | null }[] }[];
    };
    for (const report of reportList.reports) {
      received.push(await (await apiAs(request, ids.manager, `reports/${report.id}`)).text());
      for (const line of report.lines) {
        if (line.statement_id) received.push(await (await apiAs(request, ids.manager, `statements/${line.statement_id}`)).text());
      }
    }
    const state = await barState(request);
    received.push(...state.notices.filter((n) => n.to === "Operations director").map((n) => n.text));
    for (const words of DRAFT_WORDING) {
      for (const text of received) expect(text).not.toContain(words);
    }
  });

  await test.step("Pause sends nothing", async () => {
    await openAs(page, MANAGER, "/assistant/settings");
    const pause = page.getByRole("region", { name: "Pause", exact: true });
    await pause.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(pause).toContainText("Your assistant is paused.");

    const before = await sent(request);
    // Thursday 10 December: owners would be asked again, and follow-ups would fall due.
    await moveClock(page, "Add a day", 4);
    await expectTime(page, "2026-12-11T10:00:00+00:00");
    expect(await sent(request)).toEqual(before);
    await openAs(page, MANAGER, "/assistant");
    await expect(section(page, "Needs your approval")).toContainText("Nothing waits for your approval.");
  });

  await test.step("Others see nothing", async () => {
    const assistant = (await (await apiAs(request, ids.manager, "assistant")).json()) as { id: string };
    const reports = (await (await apiAs(request, ids.manager, "reports")).json()) as { reports: { id: string }[] };
    const reportId = reports.reports[0]!.id;

    for (const [person, id] of [
      [OTHER_MANAGER, ids.otherManager],
      [LEGAL, ids.legal],
      [ADMINISTRATOR, ids.administrator],
    ] as const) {
      await openAs(page, person, "/assistant");
      await expect(page.getByText(NO_ASSISTANT)).toBeVisible();
      await expect(page.locator("main")).not.toContainText(ITEM);
      for (const path of [`assistants/${assistant.id}`, `reports/${reportId}`]) {
        const response = await apiAs(request, id, path);
        expect(response.status(), `${person} reads ${path}`).toBe(404);
      }
    }
  });

  await test.step("Hand-over starts fresh", async () => {
    const shown = await controls(page);
    await shown.getByLabel("Hand over the assistant of").selectOption({ label: "Operations director" });
    await shown.getByLabel("To", { exact: true }).selectOption({ label: "Sales director" });
    await shown.getByRole("button", { name: "Hand over", exact: true }).click();
    await expect(shown.getByLabel("Hand over the assistant of")).toContainText("Sales director");
    await hideControls(page);

    await openAs(page, OTHER_MANAGER, "/assistant");
    await expect(page.getByRole("region", { name: "How you work" })).toBeVisible();
    await expect(page.locator("main")).not.toContainText(ITEM);

    const fresh = async (path: string) => (await (await apiAs(request, ids.otherManager, path)).json()) as Record<string, unknown>;
    expect(((await fresh("action-items")).items as unknown[]).length).toBe(0);
    expect((await fresh("requests")).count).toBe(0);
    expect((await fresh("reports")).count).toBe(0);
    expect(((await fresh("status")).lines as unknown[]).length).toBe(0);

    await openAs(page, MANAGER, "/assistant");
    await expect(page.getByText(NO_ASSISTANT)).toBeVisible();
  });
});
