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

## Open items

- The front end, the demonstration app and the remaining epics are not part of the
  Foundation epic.
