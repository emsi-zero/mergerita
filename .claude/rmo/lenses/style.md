# Lens: style — readable, idiomatic, consistent with this repository

- **Ceiling:** Medium · **Min confidence:** 0.8
- **Reads:** the diff and its neighbours in the same module. Formatting, import
  order and line length belong to black, isort and pylint. Do not report them.

## Checklist — answer every item

1. **Layering** — business logic is in a `reviews/` (or `audit/`, `integrations/`)
   service module. Views, admin actions, management commands and outbox handlers
   only parse input, call a service, and render or report.
2. **Names** — functions say what they do in domain words (MR, area review,
   booking, merge owner, window, verdict). No `data`, `info`, `handle_stuff`, `utils`.
   Units are in the name where a number has one (`_minutes`, `_seconds`, `_on` for dates).
3. **Docstrings** — a public service function says what it guarantees and names the
   criterion or INV it enforces, as the existing modules do. Test docstrings start
   with the ID, then "Given …, when …, then …". Write in plain, short English sentences.
4. **Typing** — new functions have annotations. `X | None` where `None` is a real case.
5. **Settings** — a new knob is an `RMO_*` constant in `orchestrator/settings.py`, read
   from the environment only there, with a bad value failing at start-up.
6. **Admin** — `@admin.action(description=..., permissions=["change"])`, it calls a
   service, it reports with `message_user`. Domain models stay `ReadOnlyAdmin`.
7. **Templates and text** — panel templates extend `panel/base.html` and reuse
   existing partials and classes. User-facing chat and alert texts are short, and
   they are listed in the spec's Contracts.
8. **Dead weight** — no unused helper, no speculative parameter, no new layer for one
   caller, no wrapper around something Django already does.
