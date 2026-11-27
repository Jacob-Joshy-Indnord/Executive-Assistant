# Executive Assistant (EA-001)

A module that will be added to Flow: a virtual employee that works as a private desk
for one manager. This repository holds part 1: the foundation, approvals, action items
and project status.

The user guide, in plain words, is in [`docs/help`](docs/help/README.md): for managers, for
owners, and what the statement marks mean.

## Start the demonstration

Two commands, one for each end, in two terminals. Nothing else is needed: no account, key,
connection or database. Install once (Python 3.11 and Node.js 20 or later):

```sh
pip install -e "backend[dev]"
(cd frontend && npm install)
```

Then start it:

```sh
cd backend && python -m standins.demo     # the back end, at http://127.0.0.1:8000
cd frontend && npm run dev                # the pages, at http://localhost:3000
```

It opens with the sample organisation, in which an assistant has just been set up for the
Operations director, so the page shows the first-time form. Everything is kept in memory.
`EA_DEMO_HOST`/`EA_DEMO_PORT` move the back end; `EA_BACKEND_URL` tells the front end where it is.

The **demonstration bar** at the top of every page plays Flow's part:

- **Person** names who is asking, as Flow's sign-in would; the page opens again as they see it.
- **Add an hour**, **Add a day** and **Go to next step** move the clock. Everything due on the
  way runs, in order, and the page follows.
- **Show controls** opens the rest: links to the pages; the **faults** of section 9.3 (the
  assistant's faulty requests or refusal to start, and approval, notices or audit records that
  fail); **As Flow**: Set up, Hand over and End an assistant and Pause or Resume everything,
  each calling `module.flow` as Flow would; the notices sent; and **Start again** from the
  sample data.

The bar is not part of the module. Its back-end addresses (`/api/demo`) are in
`backend/standins/demo.py`, and its parts are in `frontend/standins/`; neither `assistant/`
nor `components/`/`lib/` uses them (tests check both). Flow passes its own client and person
instead.

## Back end

Python 3.11, FastAPI, Pydantic 2, asynchronous throughout. No ORM; the demonstration
store uses SQLite from the standard library and SQL appears only in `assistant/store/sqlite.py`.

```
backend/
  assistant/
    models/      record shapes (section 10) and boundary shapes (section 9.1)
    rules/       pure business rules: plain sentences, visibility (BR-8)
    boundary/    what the module asks of Flow: async interfaces only
    store/       the storage interface and the SQLite demonstration store
    service/     guard (boundary failures), audit-first actions (BR-9/10), logging, visibility
    api/         one router, made from a boundary and a store
    module.py    create_module(boundary, store)
  standins/      everything that takes Flow's place, and the sample organisation
  contract/      reusable boundary and storage tests, parameterised by implementation
  tests/
```

### Checks

```sh
pip install -e "backend[dev]"
backend/check.sh          # black and ruff (100 columns), mypy, pytest with coverage (fails under 85%)
```

The tests are `unittest.IsolatedAsyncioTestCase` classes, so they also run with the
standard library alone:

```sh
cd backend && python -m unittest discover -s tests -t .
```

### Foundation decisions

- **Boundary.** `create_module` takes one `Boundary` bundle (People, WorkData, Assistant,
  Approval, Notices, Records, Clock) and wraps it so that any failure becomes
  `BoundaryUnavailable`: one plain sentence, HTTP 503, and the surrounding transaction rolls
  back. A test scans `assistant/` for network, process, machine-clock, language-model and
  secret access.
- **Person asking.** Flow names the person from the request headers
  (`People.person_asking`). The stand-in reads `X-Demo-Person: <person id>`.
- **Storage.** Every read and write happens in `store.transaction(organisation_id)`.
  `change`/`remove` take the record as it was read and raise 409 "This has already
  changed." when the version has moved on. `ConfirmedStatement` and `Report` are
  append-only types that `change`/`remove` refuse; the SQLite store also has triggers
  against UPDATE/DELETE on them. Unconfirmed statements are a separate, mutable
  `StatementDraft`.
- **Audit first.** Services write only through `service.actions.perform` / `Action`, which
  writes the audit record through the boundary before each add, change or removal. If the
  record is not written, the whole action rolls back with 503 "Not saved. Try again.".
  Records and logs hold ids, states and times only (BR-10).
- **Visibility.** `rules/visibility.py` has one rule per record kind (a test fails if a kind
  has none). Not-theirs answers 404 exactly like not-found, including malformed ids.
  `tests/test_api_visibility.py` sweeps every registered address as manager, another
  manager, owner and administrator, and fails on an address without an expectation.
- **Errors.** `{"detail": {"code", "message"}}` with 401, 404, 409, 422 or 503.

### Stand-ins

`standins.build_standins()` returns the sample organisation (Operations director, Legal
lead, Finance lead, Programme lead, Organisation administrator, Sales director; Backend
Engineer and Test Engineer), three projects and four assignments, a scripted assistant
(`faults` and `refusing` switches), the section 8.2 approval stand-in, list-backed
notices and records (`failing` switch) and a `MovableClock` (`forward_hour`,
`forward_day`, `to_next_due`) that runs every due step on the way, in time order.
`standins.schedule.connect_schedule(clock, module)` makes moving the clock call the
module's "Run what is due", as Flow's schedule would.

### Setup, approval levels and control decisions

- **Flow's calls (section 9.2)** are Python methods on `module.flow`: `set_up`, `run_due`,
  `next_due`, `finish_work`, `pause_all`, `resume_all`, `hand_over`, `end`. Records they
  write name `flow` as who made the change.
- **Set up and the form.** One assistant per manager (a second set-up or a hand-over to a
  manager who has one is refused with a reason). The first-time form has five optional
  parts; saving or skipping makes a new assistant active. Unanswered settings fall back to
  section 8.1, with the time zone of the manager's browser at first open
  (`POST /assistant/opened`). Saved answers are in every input pack.
- **Approval levels.** `rules/approval_kinds.py` registers each kind of action with its
  starting level and allowed limits. Only the manager changes a level, through
  `PUT /approval-levels/{kind}`; no tool the assistant returns can. Purchases are locked
  at not allowed, whatever is stored.
- **Colour meanings.** Administrators read and write them at `/organisation/settings`;
  anyone in the organisation reads `GET /colours`, which always lists all three colours.
- **Run what is due.** Due-step kinds register in `service/due.py`. Each step runs in its
  own transaction that first adds a `DueStepClaim` whose id is derived from the step, so the
  store refuses a second claim from any run or process (BR-13). Recurring kinds return only
  their latest due occurrence, so missed steps are done once on resume. New, paused and
  ended assistants are skipped (BR-14).
- **Pause.** By the manager (`POST /assistant/pause`, `/resume`) or by the organisation
  (`flow.pause_all`). Results that arrive while paused are kept aside unopened and handled
  in order on resume. Until Epic 3 registers its result handler, running results are only
  logged (ids).
- **Hand-over and end.** Closing handlers (`service/closing.py`, extensible) close items
  that are not done, withdraw asks, cancel running work, clear open attention entries and
  remove preferences, followed projects, chat, waiting requests, unconfirmed statements,
  kept results and levels. Confirmed statements and reports stay. A hand-over ends the
  assistant record and starts a new one with the same Flow id for the new manager, so
  nothing from before is theirs. Owners who were waited on get one notice each, after the
  change is saved; a notice that cannot be sent is logged, not retried.

### Work, checks and approvals decisions

- **Input packs** (`service/input_pack.py`, `rules/input_pack.py`). Every pack holds the saved
  preferences, the reasons refused from the last result and its kind's inputs; each part has a
  source label, a read time and the mark "data, not instructions". Kinds of work and their
  input providers register in `service/work_kinds.py`. Project status enters a pack only
  through `status_content(ConfirmedStatement)`, so drafts cannot. Size is measured on the JSON
  handed to Flow: one input at most 40,000 characters, the pack at most 200,000; longer inputs
  are cut and marked `shortened`, and inputs with no room left are counted in `left_out`.
- **Pieces of work and chat** (`service/work.py`). `POST /chat` keeps the message and queues a
  piece of work; pieces run one at a time per assistant in the order asked. A `WorkSlot` record
  with an id derived from the assistant is changed, version-checked, on every start and finish,
  so two starts at once cannot both succeed on any store. Finish is used once: a repeat finds
  the piece no longer running. Refusals and failures keep one sentence (Flow's, or a fixed one
  when Flow's is not one plain sentence) and `POST /work/{id}/retry` queues a new piece at the
  end. Messages and replies over 4,000 characters are refused with one sentence. While paused,
  messages wait and start on resume. Hand-over and end remove the chat, replies kept on
  pieces of work and the slot.
- **Checks** (`rules/tools.py`, `service/checks.py`). Tools of section 11 register with their
  fields and section 8.1 limits. Each request is checked on its own: unknown tool or field,
  missing field, text over its limit, a name that is not exactly one person or virtual employee
  of the organisation (ignoring case; the reason gives the name), a source not in the pack, an
  unknown project or item, a due date in the past. Requests after the fiftieth are refused. A
  change to an item may not move its date or owner (BR-1). The piece of work shows
  `refused_count` and `refused` (the reasons). Text is stored word for word and only ever
  returned as plain text in JSON, with `X-Content-Type-Options: nosniff`; names quoted in
  reasons are made plain.
- **Requests** (`service/requests.py`, `service/request_kinds.py`). Kinds of request register
  with their approval kind, whether they can be edited, whether they go to an owner, and the
  module's own part and its undo. Each request is checked by Flow as it is made, with level,
  limits and today's count (which includes requests allowed but not yet carried out), and is
  carried out, left waiting, or declined with Flow's reason. Approving is version-checked and
  re-checked by Flow with `approved_by_manager`, so it is carried out once; an edit keeps both
  texts and Flow is given the edited one (`ApprovalRequest.text`). Confirm-statement requests
  carry no wording and cannot be edited. Carrying out does the module's part, then asks Flow;
  if Flow cannot, the module's part is rolled back and the request fails with a reason and
  "Try again". Flow's carry-out and undo must be idempotent per request id (stated on the
  `Approval` interface and checked by the reusable boundary tests), so trying again after the
  module's transaction was lost after Flow answered carries the request out once. "Approve all" covers requests to owners only. Requests remember their
  subject's version; one whose subject is done, closed or changed is no longer listed, is
  withdrawn by a due step (or when approved), and `withdraw_for_subject` lets later services
  withdraw at once. `GET /done` lists what was carried out, undone or failed, newest first,
  with the effect, the time and whether the manager approved it; Undo is offered while it is
  possible (an added item until its owner has answered).
- **Addresses added**: `GET/POST /chat`, `GET /work/{id}`, `POST /work/{id}/retry`,
  `GET /requests`, `GET /requests/{id}`, `POST /requests/{id}/approve` (`{"text"}` to edit),
  `POST /requests/{id}/decline` (`{"reason"}`), `POST /requests/approve-all`,
  `POST /requests/{id}/retry`, `POST /requests/{id}/undo`, `GET /done`. Each is in the
  visibility sweep.

### Action items and follow-ups decisions

- **Giving an item** (`service/items.py`). `POST /action-items` is the manager's own click, so
  asking the owner to accept needs no approval (section 8.2). The ask goes out at once when it
  may (working hours, within the day's notices); otherwise it is kept "to be sent" and the
  `send_asks` due step sends it, with every ask due to the same owner sharing one notice. An item
  the manager gives themselves is open at once with no follow-ups. An item for a virtual employee
  must name one of its assignments (`GET /assignments`), is open at once and asks no one. An
  assistant's `action_item` for a virtual employee is refused with a reason, since the manager
  chooses the assignment; one for the manager is open at once when approved.
- **Owners** answer their own items only (`accept`, `propose-date` and `decline`, with a reason
  for the last two), then give one-step updates (`POST /action-items/{id}/updates`: `on_track`,
  `done` or `blocked`, a note up to 1,000 characters, another date). Words are kept and shown
  word for word with their time; `rules.items.needs_confirmation` is the BR-16 rule. An update
  answers the owner's asks and withdraws waiting requests about the item. Another person's item
  is 404; the manager answering for an owner is refused (BR-1).
- **Follow-ups** (`rules/follow_ups.py`, step `follow_up`). These are worked out on the manager's
  calendar and turned into UTC through the time zone, so 09:00 stays 09:00. The default is the
  half-way working day (rounded up) and the due date; within two working days, the due date
  only. A number chosen for all items spreads the same way, and an item's own times (up to 5, or
  none) are kept. The step sends the latest reached time only (missed ones once) as an
  `update_ask` request through the approval check. It is skipped when the owner gave an update
  in the last working day. It is noted on the item (`follow_up_note`) when asking owners is not
  allowed or Flow declines. It waits (`follow_up_waiting_until`) for working hours, or for the
  next working day when the owner has had the day's notices. Notices are counted per distinct
  minute of asks sent today, plus follow-ups waiting for approval. Runs of the due runner
  call `after_run` hooks, which carry out what Flow allowed without approval.
- **Attention entries** (`service/attention.py`). An item has one open entry at most: a new
  trigger updates it, and its id is derived from the item and its count of entries, so two
  stores' transactions cannot both add one. The triggers are `item_overdue` (end of the working
  day of the due date, plus "late after" working days), `ask_unanswered` (`answer_by`), Blocked,
  declined, another date, `owner_left` (Flow no longer knows the owner: their asks are
  withdrawn) and, for linked items, review or a missing assignment. An update clears the entry
  when it settles the reason (`rules.items.settles`): any answer settles "no answer", On track or
  Done settles Blocked, and only Done settles a late item.
- **Decisions** (`service/decisions.py`, `POST /attention/{id}/decide`). These are the manager's
  own clicks, so none needs approval: Extend (follow-ups worked out again), "Use the new date" /
  "Keep the first date", Reassign, Close and Take it up. Each marks the entry decided with the
  decision and whom it concerned. The owner is told once the change is saved; a failed notice is
  logged, not retried. Take it up sends the message the module prepared from a template (never
  written by the assistant), as the manager edited it, to the person they pick. It is recorded
  first, and nothing is kept if Flow does not deliver it. The item's controls on its page do the
  same and decide any open entry. For an entry the assistant raised, the choice becomes a
  `follow_on` piece of work with a "The manager's decision" input (kind `choice`). Audit records
  hold only the position of the choice, never its words.
- **Assignments** (step `assignment_state`). Each run reads the manager's assignments once, and a
  linked item whose assignment changed gets one step, claimed by the item's version and the new
  state. In progress, blocked and approved become On track, Blocked and Done updates (source
  `assignment`, no confirmation). Blocked also raises the entry, and approved makes the item done.
  Waiting for review and missing raise the entry; other states leave the item unchanged. When
  Flow cannot be read, the step waits for the next run.
- **Addresses added**: `GET/POST /action-items`, `GET /my-action-items`, `GET /assignments`,
  `GET /action-items/{id}`, `POST /action-items/{id}/accept`, `/propose-date`, `/decline`,
  `/updates`, `PUT /action-items/{id}/follow-ups`, `POST /action-items/{id}/extend`,
  `/reassign`, `/close`, `/done`, `GET /attention`, `GET /attention/{id}` and
  `POST /attention/{id}/decide`. Each is in the visibility sweep.

### Project status decisions

- **Following** (`service/status.py`). `POST /projects/{id}/follow` takes a project the manager
  can see (any other is 404). The owner confirms its status unless the manager names another
  person (`confirmer_id`, or later `PUT /projects/{id}/confirmer`); virtual employees cannot
  confirm. Changing who confirms withdraws what the previous person was asked about it and
  replaces their drafts. `POST /projects/{id}/unfollow` stops following, and in both cases a
  status request still waiting for approval that no longer fits is withdrawn.
- **News since the last report** (agreed with the manager). The window runs from the last report
  (scheduled or "Report now"), or from when the project was followed. Work data assignments
  carry an optional `project_id`. The `project_assignments` step reads them on every run and
  keeps every change to an assignment on a followed project, whether or not an item links to
  it. A project's news is the updates on its action items, those assignment changes, and its
  spend when it differs from the spend at the start of the window. At the time to draft
  (`status_drafts`), one `status_drafts` piece of work gets a `project_news` input for each
  project that has news; a project with none gets no draft.
- **Asking** (`status_asks`, request kind `status_round`). Each confirmer gets one request
  through the approval check, covering every project they have not confirmed since the last
  report. It carries no wording (BR-4) and cannot be edited. Carrying it out gives each project
  the assistant's draft to confirm (replacing an older unanswered one), or else asks for the
  status, starting from the last confirmed statement (its colour preselected) or an empty form.
  The request is needed by the next report. Undo is possible until the confirmer answers.
- **Owners** (`/status-requests`, for the person asked only, because a request may hold a
  draft). An answer needs a colour the owner chooses and a reason (300 and 500 character
  limits). An unchanged draft is confirmed with the assistant as author. A changed one is
  corrected: the owner is the author, the draft's wording is kept in `unconfirmed_wording` and
  never shown, and `changed_points` names what changed. A status the owner writes counts as
  confirmed (BR-16). `POST /statements/{id}/correct` adds a correction to the latest version;
  versions stay append-only, and the latest is the head of the version chain.
- **Reports** (`rules/reports.py`, `status_report`). Reports are weekly, every two weeks (from
  the week the manager took the assistant on), monthly on the first chosen weekday, or off. Ask
  and draft times are 09:00 the chosen number of working days, and one more, before the report.
  Every time is local to the manager, and missed ones are done once. A report has one line per
  followed project the manager can still see. A project whose confirmer was asked after its
  latest confirmation shows that confirmation as Awaiting confirmation. Colour changes are
  listed against the previous report. `GET /status` shows the live lines and the next report
  time.
- **Follow-through**. A project the manager can no longer see is removed by
  `project_visibility`, and the manager gets one notice. A confirmer who leaves raises the
  project's attention entry (Change who confirms, Stop following). Two reports in a row
  without a confirmation raise one with Ask again, Change who confirms and Stop following; a
  confirmation clears it. `GET /statements/{id}` shows a correction's author, time and
  changed points, never the unconfirmed wording.
- **Addresses added**: `GET /projects`, `POST /projects/{id}/follow`,
  `PUT /projects/{id}/confirmer`, `POST /projects/{id}/unfollow`, `GET /status`,
  `GET/POST /reports`, `GET /reports/{id}`, `GET /status-requests`,
  `GET /status-requests/{id}`, `POST /status-requests/{id}/answer` and
  `POST /statements/{id}/correct`. Each is in the visibility sweep.

### Meeting notes decisions (part 2, Epic 1)

- **Two ways in, one path.** An organiser shares a transcript (`POST /transcripts`, any person in
  the organisation, with the two ticks), or the assistant attends a meeting and the meeting
  service delivers it. Either way it becomes a `Meeting` with numbered parts (`rules/meetings.py`:
  times for .vtt and attended meetings, line numbers for text), a `meeting_notes` piece of work
  whose pack holds the parts as data, and the same checks, review and removal.
- **Sharing** (`service/meetings.py`). Uploads arrive as JSON: a .vtt or .txt file as its name
  and base64 bytes, or pasted text. They are read in memory only, never written to disk or run,
  and each refusal has its own sentence (`rules.meetings.SHARING_REFUSALS`). Meeting notes are
  off until an administrator allows them (`PUT /organisation/settings`
  `{"meeting_notes_allowed": true}`). 'Write notes from a shared transcript' starts at with
  approval and can be limited to projects (`limits.projects` on `PUT /approval-levels/{kind}`);
  the approval request carries the projects the transcript names, and outside the limit it
  waits for the manager (never refused). Not allowed refuses the sharing and keeps nothing.
- **Withdrawal and the organiser's list.** The organiser sees only title and state
  (`GET /transcripts`) and withdraws while shared, writing or failed: the text goes, a waiting
  request closes, the manager is told the title. Notes arriving afterwards are refused as
  discarded.
- **Checks** (`MeetingService.take_notes`). Every line is checked alone: 300 characters (600 for
  the summary), and every cited part must exist. An unmatched owner is kept as "Owner not
  found"; a statement about a project not followed is dropped and counted. Once the notes are
  kept, the transcript's text and speakers are removed (numbers and times stay), each statement
  becomes a draft for its confirmer through the approval check, and the manager gets one notice.
  A piece of work that ends without notes fails the meeting with its reason;
  `POST /meetings/{id}/retry` tries again while the text is kept.
- **7 days** (`transcript_text_removal`). This due step is the only one that also runs for new
  and paused assistants (`runs_while_paused`), because BR-3's 7 days are the latest. A meeting
  still shared, writing or failed is then removed without notes, and the manager is told.
- **Review** (`service/meeting_notes.py`). Keep, Edit and Remove are the manager's own clicks (no
  approval). Keep uses the same checks and the same `_give_to` as a hand-given item (origin
  `meeting`; a virtual employee needs `assignment_id`). Statements show only "Awaiting its owner"
  until confirmed, and then the confirmed words. Each kept line shows its item's outcome
  (`done`, `closed` or `raised`) and its date.
- **Attending** (`service/attendance.py`). The boundary gains `Meetings` (upcoming, join, leave,
  transcript) and `Knowledge` (add, remove), and names no product. Attend for me books one
  meeting or a series without approval. The `attend_meeting` tool goes through 'Attend a
  meeting and take notes' (with approval). A meeting the manager does not organise asks its
  organiser (`/meeting-requests`, a person request with `asks: "attend"`). The attendance
  record holds ids, times and states only. Due steps, each claimed once:
  `meeting_withdrawals` (cancelled or over), `meeting_start` (no answer → withdrawn, else join as
  "<manager>'s assistant (notes)"), `meeting_admission` (asks again every 2 minutes, stops at 10),
  and `meeting_end` (leave, then ask for the transcript every 10 minutes; failed at 2 hours).
  Removal by the organiser, on Requests for you or in the meeting (the leave answer's
  `removed_at`), leaves the meeting, and only what was said before is used.
- **Remembered meetings.** `GET /meeting-notes/search` (words, meeting, project, person) never
  matches unconfirmed statements. A chat message naming a past meeting adds its notes to the
  pack as data (`meeting_notes` inputs), so the reply can cite them.
  `POST /meeting-notes/{id}/share` and `/unshare` tell Flow through Knowledge, and deleting a
  shared note takes it back. Hand-over and end never call Knowledge.
- **Stand-ins**: `standins/meetings.py` (three meetings for the Operations director; switches
  `kept_waiting`, `join_refused`, `no_transcript`; the transcript 10 minutes after the end),
  `standins/knowledge.py` (a list) and `standins/sample_transcript.py` (the vendor review).
- **Addresses added**: `POST/GET /transcripts`, `POST /transcripts/{id}/withdraw`,
  `GET /meetings`, `GET /meetings/upcoming`, `POST /meetings/upcoming/{ref}/attend`,
  `GET /meetings/bookings`, `GET /meetings/bookings/{id}`, `GET /meetings/{id}`,
  `POST /meetings/{id}/retry`, `GET /meeting-notes`, `GET /meeting-notes/search`,
  `GET/DELETE /meeting-notes/{id}`, `POST /meeting-notes/{id}/actions/{action}/keep`,
  `PUT /meeting-notes/{id}/actions/{action}`, `POST .../remove`, `POST /meeting-notes/{id}/share`,
  `/unshare`, `GET /meeting-requests`, `GET /meeting-requests/{id}` and
  `POST /meeting-requests/{id}/accept`, `/decline`, `/remove`. Each is in the visibility sweep.

## Front end

Next.js 15 (App Router), React 19, TypeScript in strict mode, Tailwind CSS 3 and
`lucide-react`; no other state, data or component library.

```
frontend/
  app/assistant/          the pages: each renders one component, given its base path and client
  components/assistant/   the pages, the Assistant page's sections and the shared parts
  lib/assistant/          the API client, the types, the tokens and the refresh hook
  standins/               the demonstration bar and the client that names the person (not the module)
  tests/                  Vitest + React Testing Library
```

### Checks

```sh
cd frontend
npm install
npm run check             # ESLint (Next.js rules), tsc --noEmit, vitest run
npm run dev               # pages at /assistant; /api/assistant and /api/demo go to EA_BACKEND_URL
```

### End-to-end run (Story 7.2)

```sh
cd frontend
npm run e2e               # Playwright: starts both ends itself, on ports 8010 and 3010
```

One test, `e2e/story-7-2.spec.ts`, drives the demonstration through the bar with the Chromium
already in the build environment (`PLAYWRIGHT_BROWSERS_PATH`; `@playwright/test` is pinned to
match it). It never waits on the real clock: the demonstration's clock is moved through the bar
and the browser's date is fixed to the demonstration's start. `PYTHON` names the interpreter
that has the back end installed (default `python3`). The run: nothing is sent until the form
is saved; an item for the Legal lead is accepted, followed up (approved), answered Blocked and
extended; three projects go through two reports, the second with one line confirmed, one
corrected by its owner and one awaiting confirmation, and the assistant's unconfirmed wording
appears nowhere the manager can read; pausing sends nothing; another manager, an owner and the
administrator find nothing; and a hand-over starts the new manager at the form with nothing
from before. The bar's **Work data** control (an assignment's state) gives projects news, so
the assistant drafts statements.

### Screens decisions

- **Shared parts** (`components/assistant/parts.tsx`) hold every control with the section 13.3
  names and properties (`Btn`, `IconBtn`, `Input`, `Select`, `Textarea`, `Toggle`, `Badge`,
  `Avatar`, `SecHead`, `Block`, `Panel`, `Field`, `FormActions`, `List`, `ListRow`, `Kpi`,
  `Empty`, `Dialog`), plus `ColourName` (a status colour always named in words), `ChoiceGroup`
  (radio choices) and `ErrorLine`. `tests/source-rules.test.ts` fails if a screen draws its own
  control, uses a colour or text size outside section 13.4, sets a width wider than 375px, or
  names an outside address.
- **Tokens** (`lib/assistant/tokens.ts`) are the section 13.4 table. Tailwind exposes them as
  `brand-*` colours that accept an opacity; `app/globals.css` holds the light and dark values,
  switched by a `dark` class on the page. A test checks WCAG AA contrast in both themes. Fonts
  are the system's own.
- **API client** (`lib/assistant/client.ts`): `createAssistantClient({ baseUrl, fetch })`. Every
  page takes `client` and `basePath` as properties, so Flow passes its own; links come from
  `assistantPaths(basePath)`. A failure is shown as its one-sentence `detail.message`.
- **Quiet refresh** (`useResource`, `useVisibleRefresh`): every 30 seconds while the page is
  visible, and at once when it becomes visible again. Data is replaced in place, so open parts,
  typed text and focus stay; a failed refresh keeps what is shown.
- **The Assistant page** builds its parts from one registry (`components/assistant/sections`)
  in section 13.1 order; part 2 registers more. Approve, edit, decline and decide take the row
  out at once (the count changes with it) and bring it back with the reason if the call fails.
  Closing what was opened in place returns focus to its opener; dialogs trap and return focus.
- **Top bar**: `WaitingCount` / `useWaitingCount` / `waitingCount` read `GET /waiting-count`
  (requests to approve plus open attention entries).
- **Manager notices** (`service/manager_notices.py`): after each run of what is due, each
  result, each resume and each owner's answer, the manager gets one notice for every scheduled
  report and attention entry made since the last one. A version-checked `ManagerNoticeMark` per
  assistant makes each notice go once. Nothing is sent while new or paused; the text holds
  counts only; "Report now" is the manager's own click and is not told.
- **Addresses added for the screens**: `GET /waiting-count`, `GET /people?name=` (people are
  named, not addressed: exact name, ignoring case), `GET /my-statements` (the owner's latest
  confirmed versions, for Correct). `GET /my-action-items` now says who asks and, for a sent
  ask, by when; `GET /work/{id}` includes the reply. Each is in the visibility sweep.

## Open items

- The 375px, light/dark and nothing-from-another-site checks of Epic 6 ran by hand in Chromium
  against the seeded world; the Playwright run of Story 7.2 covers the flow, not those checks.
- Epic 7 was built without running the test suites or the Playwright run (the build machine
  could not take them); `backend/check.sh`, `npm run check` and `npm run e2e` must be run before
  merging.
- A request allowed and committed but not yet carried out when a process stops is carried out
  on the next resume of that assistant or the next result it finishes; there is no separate
  due step for it.
- When the assistant cannot be reached to start a piece of work, that piece fails (and can be
  tried again) and the rest of the queue waits for the next message, result, retry or resume.
- Epics 3 to 5 were verified with the standard-library test runner only: black, ruff, mypy,
  pytest and coverage could not be installed in the build environment (no network). Run
  `backend/check.sh` before merging.
- The `owner_left`, `assignment_state`, `project_visibility`, `confirmer_left` and
  `project_assignments` steps ask Flow (People, Work data) on every run while deciding what is
  due. That is fine for the stand-ins; a large organisation would want Flow to
  push these changes instead.
- Notices on hand-over or end are sent after the change is saved; one that fails is not
  retried, so its owner is not told.
- Assignment changes are seen when a run reads them: a change that is undone between two runs
  is not news. "Report now" also starts a new window and counts towards two reports in a row
  without a confirmation.
- Meeting notes (part 2, Epic 1) were verified with the standard-library test runner only;
  black, ruff and mypy were not run. Run `backend/check.sh` before merging.
- Removing everything at hand-over or end (notes, transcripts, bookings, organiser requests,
  leaving a meeting) is the Learning epic's task "Remove everything at handover or end". Until
  then, part 1's closing handlers already withdraw organiser asks and cancel running work.
- A shared transcript is cut to fit the input pack (200,000 characters). The parts left out are
  counted in `left_out`, so a very long transcript gets notes from its first parts only.
- `meeting_withdrawals` asks the meeting service on every run while any booking waits, like the
  part 1 steps that read Flow.
