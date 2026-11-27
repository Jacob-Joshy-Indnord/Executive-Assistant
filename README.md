# Executive Assistant (EA-001)

A module that will be added to Flow: a virtual employee that works as a private desk
for one manager. This repository holds part 1: the foundation, approvals, action items
and project status.

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
backend/check.sh          # black, ruff, mypy, pytest with coverage (fails under 85%)
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

## Open items

- The front end, the demonstration app and the epics after Epic 4 are not built yet.
- A request allowed and committed but not yet carried out when a process stops is carried out
  on the next resume of that assistant or the next result it finishes; there is no separate
  due step for it.
- When the assistant cannot be reached to start a piece of work, that piece fails (and can be
  tried again) and the rest of the queue waits for the next message, result, retry or resume.
- Epics 3 and 4 were verified with the standard-library test runner only: black, ruff, mypy,
  pytest and coverage could not be installed in the build environment (no network). Run
  `backend/check.sh` before merging.
- The `owner_left` and `assignment_state` steps ask Flow (People, Work data) on every run while
  deciding what is due. That is fine for the stand-ins; a large organisation would want Flow to
  push these changes instead.
- Notices on hand-over or end are sent after the change is saved; one that fails is not
  retried, so its owner is not told.
