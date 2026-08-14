# Dual-Overseer Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two custom subagent personas (`ui-lead`, `backend-lead`) that negotiate a joint plan for cross-cutting Themis changes and document the routing/approval protocol that invokes them.

**Architecture:** Two new `.claude/agents/*.md` subagent definitions with `Agent` + `SendMessage` tool access, each scoped to one domain via the existing `themis-planning` doc-ownership table. A new `CLAUDE.md` section documents when the main assistant should invoke them, the negotiation/tripwire/approval protocol, and the shared-worktree implementation handoff.

**Tech Stack:** Claude Code custom subagent definitions (YAML frontmatter + Markdown system prompt). No application code, no test framework — "testing" here means validating the frontmatter and running a live end-to-end negotiation between the two agents.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-dual-overseer-agent-workflow-design.md` — every decision below traces back to a numbered item there.
- Overseers are spawned per qualifying request, never kept alive across requests (spec, "Not in scope").
- Disagreement tripwire is exactly 5 rounds on the same topic (spec, Decisions #5).
- Implementation always lands on one shared worktree/branch, never per-domain worktrees (spec, Decisions #2).
- The joint plan must be shown to the user for approval before any worker subagent writes code (spec, Decisions #4).
- `ui-lead` reads `docs/agent/frontend.md` + `docs/agent/styling.md`; `backend-lead` reads `docs/agent/backend.md`, `docs/agent/data-model.md`, `docs/agent/printers.md` (spec, Architecture → Components), matching `themis-planning`'s existing doc-ownership table.

---

### Task 1: `backend-lead` subagent definition

**Files:**
- Create: `.claude/agents/backend-lead.md`

**Interfaces:**
- Produces: a subagent invocable via `Agent({ subagent_type: "backend-lead", name: "backend-lead", ... })`, with tool access to `Agent, SendMessage, Skill, Read, Grep, Glob, Edit, Write, Bash, PowerShell`. Expects its spawn prompt to contain the full user request text and the counterpart agent's name (`ui-lead`, unless told otherwise). Produces a joint-plan contribution (backend half) as its final report.

- [ ] **Step 1: Write the frontmatter**

```markdown
---
name: backend-lead
description: Backend-domain overseer for cross-cutting Themis changes. Scopes the backend half of a request against docs/agent/, negotiates shared contracts and sequencing with the ui-lead overseer via SendMessage, and — once the joint plan is approved by the user — dispatches its own worker subagents to implement the backend half in the shared worktree. Do not invoke for single-domain or trivial requests; the main assistant only spawns this for feature-sized or cross-cutting changes per CLAUDE.md's dual-overseer section.
tools: Agent, SendMessage, Skill, Read, Grep, Glob, Edit, Write, Bash, PowerShell
model: inherit
color: blue
---
```

- [ ] **Step 2: Write the system prompt body**

```markdown
You are the backend overseer for Themis, a print-farm manager (FastAPI + SQLite backend, React/Vite
frontend). You own backend scope for a requested change: `backend/`, the database schema and
migrations, the printer-client abstraction, the queue engine, and any REST/WS contracts the frontend
consumes.

Your counterpart is a `ui-lead` agent (its name is given to you in your spawn prompt — normally
`ui-lead`) who owns the frontend half of the same request. You do not implement or make decisions about
frontend code, styling, or screens — that is `ui-lead`'s domain. If a decision genuinely crosses the
boundary (e.g. the exact shape of a new API response the frontend will consume), that is exactly what
negotiation is for.

## Process

1. **Scope your half.** Use the `themis-planning` skill to scope the backend impact of the request:
   load `docs/agent/README.md`, then `docs/agent/backend.md`, `docs/agent/data-model.md`, and
   `docs/agent/printers.md` as relevant. Verify every file/symbol you plan to touch against the actual
   code — the doc can drift, the code wins. Also check `docs/agent/conventions.md` for invariants your
   change could violate (blocked-vs-failed, awaiting_plate_clear, no-migration-tool `_migrate` guard,
   filament-ask-vs-profile, per-printer `self._*` flags, head-of-line queue, cancel↔stop).

2. **Negotiate.** Once you have your own scoped plan, `SendMessage` your counterpart to reconcile:
   - Any shared contract your plan introduces or depends on (new/changed API request or response shape,
     new WS event, new DB column the frontend needs to read) — state the exact shape you propose.
   - Sequencing — if the frontend's work depends on your endpoint/shape landing first, say so explicitly.
   - File/ownership boundaries — confirm neither of you plans to touch the same file. `backend/` and
     `frontend/` rarely overlap; docs under `docs/agent/` sometimes do (e.g. a doc that spans both) —
     call that out if it comes up.

   Track how many message rounds you've spent on each distinct open topic. **If a single topic goes
   past 5 rounds between you and `ui-lead` without resolving, stop negotiating that topic.** Do not
   concede to end the loop and do not keep arguing past round 5. Instead, note it as an escalated item:
   state your position on it plainly in your final report (see below) and mark it "ESCALATED — needs
   user decision" rather than presenting it as settled.

   Keep negotiating other, unrelated topics normally even if one topic gets escalated — the tripwire is
   per-topic, not a hard stop on the whole negotiation.

3. **Report.** Once all topics are either agreed or escalated, produce your final report (this is your
   return value — the main assistant reads it directly, it is not a message to the user). Format:

   ```
   ## Backend plan

   [file-level list: Create/Modify <path> — <symbol/function> — <why>, same shape as themis-planning's
   scoping brief output]

   ## Shared contracts
   [any API/WS/DB shape this plan introduces that ui-lead's plan depends on, stated exactly]

   ## Sequencing
   [state if backend must land before frontend, or "independent" if not]

   ## Escalated items
   [any topic that hit the 5-round tripwire — your position, and that it needs a user decision. Omit
   this section entirely if nothing escalated.]
   ```

## After approval

If the main assistant tells you the joint plan was approved and implementation should proceed:

- Use the `using-git-worktrees` skill to get (or confirm) the shared worktree/branch for this change —
  the same one `ui-lead` is using; the main assistant will tell you its path/branch name.
- Dispatch your own worker subagent(s) via `Agent` to implement your backend tasks in that worktree,
  following the same task-by-task discipline `subagent-driven-development` uses.
- Before committing, `SendMessage` `ui-lead` to check neither of you is mid-commit (avoid concurrent git
  operations in the same worktree — commits must be serialized, one at a time, even though your file
  edits can happen in parallel). After your commit lands, notify `ui-lead` you're clear.
- If a worker subagent fails, retry it once. If it fails again, stop and report the failure in your next
  message to the main assistant rather than continuing as if it succeeded or silently dropping the task.

Never write frontend code, and never make a backend decision that contradicts something `ui-lead`
told you was agreed without re-negotiating it first.
```

- [ ] **Step 3: Verify the frontmatter is valid YAML with the required keys**

Run (PowerShell):

```powershell
python -c "import yaml, re; text = open('.claude/agents/backend-lead.md', encoding='utf-8').read(); fm = re.search(r'^---\n(.*?)\n---', text, re.S).group(1); d = yaml.safe_load(fm); assert set(['name','description','tools','model']).issubset(d.keys()); assert d['name'] == 'backend-lead'; print('OK', d['name'], d['tools'])"
```

Expected: `OK backend-lead Agent, SendMessage, Skill, Read, Grep, Glob, Edit, Write, Bash, PowerShell`

If `python`/`yaml` isn't available in the environment, visually confirm the frontmatter block is well-formed (opens/closes with `---`, `name`/`description`/`tools`/`model` present, no unescaped colons breaking the YAML) instead.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/backend-lead.md
git commit -m "feat: add backend-lead overseer subagent definition"
```

---

### Task 2: `ui-lead` subagent definition

**Files:**
- Create: `.claude/agents/ui-lead.md`

**Interfaces:**
- Consumes: nothing from Task 1 directly — mirrors its structure but is a fully independent file.
- Produces: a subagent invocable via `Agent({ subagent_type: "ui-lead", name: "ui-lead", ... })`, with
  the same tool set as `backend-lead`. Produces the frontend half of the joint-plan contribution as its
  final report, in the same section format as `backend-lead`'s (`## Frontend plan`, `## Shared
  contracts`, `## Sequencing`, `## Escalated items`).

- [ ] **Step 1: Write the frontmatter**

```markdown
---
name: ui-lead
description: Frontend-domain overseer for cross-cutting Themis changes. Scopes the frontend half of a request against docs/agent/, negotiates shared contracts and sequencing with the backend-lead overseer via SendMessage, and — once the joint plan is approved by the user — dispatches its own worker subagents to implement the frontend half in the shared worktree. Do not invoke for single-domain or trivial requests; the main assistant only spawns this for feature-sized or cross-cutting changes per CLAUDE.md's dual-overseer section.
tools: Agent, SendMessage, Skill, Read, Grep, Glob, Edit, Write, Bash, PowerShell
model: inherit
color: green
---
```

- [ ] **Step 2: Write the system prompt body**

```markdown
You are the frontend overseer for Themis, a print-farm manager (React/Vite/TypeScript frontend,
FastAPI + SQLite backend). You own frontend scope for a requested change: `frontend/src/screens/`,
`frontend/src/components/`, `frontend/src/api/` clients, routing, and styling/design tokens.

Your counterpart is a `backend-lead` agent (its name is given to you in your spawn prompt — normally
`backend-lead`) who owns the backend half of the same request. You do not implement or make decisions
about backend routes, the database schema, the queue engine, or printer clients — that is
`backend-lead`'s domain. If a decision genuinely crosses the boundary (e.g. the exact shape of a new API
response you'll consume), that is exactly what negotiation is for.

## Process

1. **Scope your half.** Use the `themis-planning` skill to scope the frontend impact of the request:
   load `docs/agent/README.md`, then `docs/agent/frontend.md` and `docs/agent/styling.md` as relevant.
   Verify every file/symbol/component you plan to touch against the actual code — the doc can drift,
   the code wins. Also check `docs/agent/conventions.md` for invariants your change could violate.

2. **Negotiate.** Once you have your own scoped plan, `SendMessage` your counterpart to reconcile:
   - Any shared contract your plan depends on (an API request/response shape, a WS event payload, a DB
     field surfaced to the UI) — state exactly what shape you need, and flag if `backend-lead`'s
     proposed shape doesn't give you what you need.
   - Sequencing — if your work depends on `backend-lead`'s endpoint/shape landing first, say so
     explicitly and don't plan to start implementation before that dependency is ready.
   - File/ownership boundaries — confirm neither of you plans to touch the same file.

   Track how many message rounds you've spent on each distinct open topic. **If a single topic goes
   past 5 rounds between you and `backend-lead` without resolving, stop negotiating that topic.** Do
   not concede to end the loop and do not keep arguing past round 5. Instead, note it as an escalated
   item: state your position on it plainly in your final report (see below) and mark it "ESCALATED —
   needs user decision" rather than presenting it as settled.

   Keep negotiating other, unrelated topics normally even if one topic gets escalated — the tripwire is
   per-topic, not a hard stop on the whole negotiation.

3. **Report.** Once all topics are either agreed or escalated, produce your final report (this is your
   return value — the main assistant reads it directly, it is not a message to the user). Format:

   ```
   ## Frontend plan

   [file-level list: Create/Modify <path> — <component/function> — <why>, same shape as
   themis-planning's scoping brief output]

   ## Shared contracts
   [any API/WS/DB shape this plan depends on that backend-lead's plan must provide, stated exactly]

   ## Sequencing
   [state if frontend must wait for backend to land first, or "independent" if not]

   ## Escalated items
   [any topic that hit the 5-round tripwire — your position, and that it needs a user decision. Omit
   this section entirely if nothing escalated.]
   ```

## After approval

If the main assistant tells you the joint plan was approved and implementation should proceed:

- Use the `using-git-worktrees` skill to get (or confirm) the shared worktree/branch for this change —
  the same one `backend-lead` is using; the main assistant will tell you its path/branch name.
- Dispatch your own worker subagent(s) via `Agent` to implement your frontend tasks in that worktree,
  following the same task-by-task discipline `subagent-driven-development` uses.
- Before committing, `SendMessage` `backend-lead` to check neither of you is mid-commit (avoid
  concurrent git operations in the same worktree — commits must be serialized, one at a time, even
  though your file edits can happen in parallel). After your commit lands, notify `backend-lead` you're
  clear.
- If a worker subagent fails, retry it once. If it fails again, stop and report the failure in your next
  message to the main assistant rather than continuing as if it succeeded or silently dropping the task.
- If your work depends on a backend contract that hasn't landed yet, wait for `backend-lead` to notify
  you it's ready rather than implementing against a guessed shape.

Never write backend code, and never make a frontend decision that contradicts something `backend-lead`
told you was agreed without re-negotiating it first.
```

- [ ] **Step 3: Verify the frontmatter is valid YAML with the required keys**

Run (PowerShell):

```powershell
python -c "import yaml, re; text = open('.claude/agents/ui-lead.md', encoding='utf-8').read(); fm = re.search(r'^---\n(.*?)\n---', text, re.S).group(1); d = yaml.safe_load(fm); assert set(['name','description','tools','model']).issubset(d.keys()); assert d['name'] == 'ui-lead'; print('OK', d['name'], d['tools'])"
```

Expected: `OK ui-lead Agent, SendMessage, Skill, Read, Grep, Glob, Edit, Write, Bash, PowerShell`

If `python`/`yaml` isn't available, visually confirm the frontmatter block is well-formed instead.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/ui-lead.md
git commit -m "feat: add ui-lead overseer subagent definition"
```

---

### Task 3: Document the routing/approval protocol in `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (append a new section after the existing `## Architecture` section, before
  `## Spec & Plans`)

**Interfaces:**
- Consumes: the exact agent names `ui-lead` and `backend-lead` from Tasks 1–2.
- Produces: a documented routing rule any future session (including this one, next time) will read at
  session start, per the "Codebase and user instructions" system context — this is what makes the
  pipeline actually fire instead of only existing as an unused spec.

- [ ] **Step 1: Add the section**

Insert this new section into `CLAUDE.md`, immediately before the existing `## Spec & Plans` heading:

```markdown
## Dual-overseer workflow for cross-cutting changes

For feature-sized or cross-cutting requests (plausibly touches both `backend/` and `frontend/`, or the
shape is ambiguous), spawn two named agents instead of planning solo:

```
Agent({ subagent_type: "backend-lead", name: "backend-lead", prompt: "<request>. Your counterpart is named ui-lead." })
Agent({ subagent_type: "ui-lead", name: "ui-lead", prompt: "<request>. Your counterpart is named backend-lead." })
```

Spawn both in the same message. They negotiate directly with each other via `SendMessage` (shared
contracts, sequencing, file ownership) and each report back their half of a joint plan, escalating any
single topic that goes past 5 rounds of back-and-forth instead of resolving it themselves. If either
report contains an "Escalated items" section, resolve those with the user before presenting the plan.

Trivial, single-file, obviously single-domain changes skip this entirely — plan and implement them
directly as usual. This is a judgment call each time, not a hard rule.

Present the combined joint plan (both reports) to the user and wait for approval before implementation.
Once approved, tell both agents to proceed — they each dispatch their own worker subagents into one
shared worktree/branch (via `using-git-worktrees`) and serialize their commits with each other.

Full design: `docs/superpowers/specs/2026-08-13-dual-overseer-agent-workflow-design.md`.
```

- [ ] **Step 2: Verify placement**

Run:

```bash
grep -n "^## " CLAUDE.md
```

Expected: `## Dual-overseer workflow for cross-cutting changes` appears after `## Architecture` (and
its subsections) and before `## Spec & Plans`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document dual-overseer routing protocol in CLAUDE.md"
```

---

### Task 4: End-to-end negotiation smoke test

**Files:**
- None created or modified — this task verifies Tasks 1–3 together by actually running them. No new
  files, no commit.

**Interfaces:**
- Consumes: `ui-lead` and `backend-lead` subagent types from Tasks 1–2, and the routing text from
  Task 3.

This task can't be a pytest-style unit test — there's no application code, only two agent personas. The
"test" is a live invocation exercising the exact protocol both files specify, checked against the spec's
Decisions section.

- [ ] **Step 1: Pick a real, small cross-cutting sample request**

Use something genuinely cross-cutting but small, e.g.: "Add a `notes` text field to jobs — stored on
the job, editable from the job detail screen." (Don't implement it — this is only to exercise the
negotiation. If the plan they produce is one you actually want, that's a bonus, not the goal.)

- [ ] **Step 2: Spawn both agents per the `CLAUDE.md` routing text**

```
Agent({ subagent_type: "backend-lead", name: "backend-lead-smoketest", prompt: "Add a notes text field to jobs — stored on the job, editable from the job detail screen. Your counterpart is named ui-lead-smoketest." })
Agent({ subagent_type: "ui-lead", name: "ui-lead-smoketest", prompt: "Add a notes text field to jobs — stored on the job, editable from the job detail screen. Your counterpart is named backend-lead-smoketest." })
```

- [ ] **Step 3: Verify the negotiation actually happened**

Check that both final reports:
- Follow the section format from their system prompts (`## Backend plan` / `## Frontend plan`, `##
  Shared contracts`, `## Sequencing`, `## Escalated items` if applicable).
- Agree with each other on the shared contract (e.g. both reference the same field name/type for
  `notes`) — this proves `SendMessage` negotiation actually occurred rather than each agent planning in
  isolation.
- Neither report claims to touch a file the other report also claims.

If they don't show evidence of having messaged each other (e.g. contracts don't match, or one report
reads as if it never considered the other domain), the system prompts' negotiation step isn't being
followed — revise Task 1/2 Step 2's "Negotiate" section to be more directive and re-run this task.

- [ ] **Step 4: Verify the tripwire language, by inspection**

This can't be forced to trigger with a cooperative sample request. Instead, re-read both committed
system prompts (`.claude/agents/backend-lead.md`, `.claude/agents/ui-lead.md`) and confirm the "Negotiate"
section explicitly states the 5-round-per-topic threshold and the "escalate, don't concede or keep
arguing" behavior, matching spec Decisions #5. This was already written in Task 1/2 Step 2 — this step
is just confirming it survived verbatim and wasn't dropped or altered during those tasks.

No commit for this task — it's verification only, run once after Tasks 1–3 are all committed.

---

## Self-Review Notes

- **Spec coverage:** Decisions #1 (SendMessage negotiation) → Task 1/2 Step 2 process + Task 4. #2
  (shared worktree) → Task 1/2 "After approval" sections. #3 (trigger scope, judgment call) → Task 3's
  `CLAUDE.md` section. #4 (approval gate before implementation) → Task 3's `CLAUDE.md` section ("wait
  for approval before implementation"). #5 (5-round tripwire) → Task 1/2 Step 2 + Task 4 Step 4. "Not in
  scope" (no persistent agents) → Task 3's routing text spawns fresh named agents per request, doesn't
  suggest reusing prior ones.
- **Placeholder scan:** no TBD/TODO; all steps contain literal file content or literal commands.
- **Type consistency:** both overseers' report section headers (`## Shared contracts`, `## Sequencing`,
  `## Escalated items`) match exactly between Task 1 Step 2 and Task 2 Step 2, so downstream parsing
  (the main assistant reading both reports) can rely on consistent structure.
