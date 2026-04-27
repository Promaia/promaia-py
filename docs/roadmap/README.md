# Roadmap

This folder tracks the active implementation roadmaps for Promaia.

Adapted from the `promaia-ts` convention so that work proceeds against
a checked-off plan instead of being rebuilt from scratch every session.

## Structure

- **`ROADMAP.md`** — Top-level index. Lists the active workstreams with
  a one-line description and a link to the detailed file.
- **Active project files** — One `.md` per workstream (e.g.
  `agent-permissions.md`). Each file lays out the work in **phases**
  with checkbox items (`- [ ]` / `- [x]`).
- **`future.md`** — Items we've deliberately deferred. Avoid leaving
  unchecked stale entries in active files; move deferred work here.
- **`archive/`** — Completed roadmaps move here, prefixed by the
  approximate completion date in ISO order
  (`YYYY-MM-DD-<slug>.md`). Archived files preserve the full history
  for reference and search.

## How to use

1. **Plan first.** Before starting non-trivial work, build (or update)
   the roadmap file. Use `docs/plans/` for deeper design notes and
   `docs/research/` for current-state surveys; the roadmap reduces
   those down to a checkable task list.
2. **Work the roadmap top-to-bottom in phases.** Each phase is a
   coherent slice. Don't skip ahead.
3. **Per-step loop**, repeated for every step in a phase:
   - Implement all items in the step
   - Run `prosecheck` and fix any issues it surfaces (loop until clean)
   - Self-review the diff (`docs/self-review.md` if a longer note is needed)
   - Update `docs/architecture/` if the change affects the architecture
   - Present the change + a short review to the user; user picks next action
4. **Check the boxes as you go.** A phase is done when all its boxes
   are `- [x]`. The roadmap file MUST reflect actual progress — don't
   leave stale unchecked items lying around (see prosecheck rule).
5. **When all phases are done**, move the file to `archive/` with a
   `YYYY-MM-DD-` prefix.
6. **When you discover new tasks during implementation**, add them to
   the appropriate phase in the active file. If they're out of scope
   for the current workstream, add them to `future.md` instead.

## Rules (enforced by prosecheck)

- Checklist items in `docs/roadmap/*.md` must reflect actual completion
  status. Items that have been implemented should be checked off
  (`- [x]`). Items that have been descoped or moved should be removed
  or relocated, **not left as unchecked stale entries**.
- Active roadmap files at the root (no date prefix) describe in-flight
  work. Date-prefixed files belong in `archive/`.

## Conventions

- File names: `kebab-case.md`. Topic-named, not date-named (dates only
  appear when archived).
- Phase headings: `## Phase 1: <title>` etc.
- Step headings under a phase: `### <Verb-led title>` (e.g.
  `### Schema + runtime gates`).
- Checkbox items are concrete enough to commit: `- [ ] Add
  <field> to <module>`, not `- [ ] Improve <thing>`.
- One commit per step is a good default; one commit per phase is fine
  for small phases. Avoid many half-finished phases at once.
