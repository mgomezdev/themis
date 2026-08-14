# Dual-Overseer Agent Workflow Design

## Context

Themis already has most of a plan → isolate → implement pipeline as skills: `themis-planning` scopes
a request against `docs/agent/`, `writing-plans` turns a scope into a task list, `using-git-worktrees`
provides isolated workspaces, and `subagent-driven-development` executes a plan with subagents. What's
missing is a way to split a cross-cutting request (touches both `backend/` and `frontend/`) into two
domain-owned plans that get negotiated against each other *before* implementation starts, instead of
one planner trying to hold both domains at once.

This design adds two standing subagent personas — a UI overseer and a backend overseer — that
negotiate a joint plan for a requested change, get it approved by the user, then each dispatch their
own worker subagents into a shared worktree to implement it.

## Decisions

Resolved through brainstorming with the user:

1. **Mechanism**: the two overseers are spawned as named agents that message each other directly
   (`SendMessage`) to negotiate, rather than a deterministic `Workflow` script or the main loop relaying
   between them.
2. **Worktree strategy**: one shared worktree/branch per requested change, not one worktree per domain.
   Backend and frontend workers commit onto the same branch; commits are serialized to avoid git index
   contention, since the directories themselves rarely overlap.
3. **Trigger scope**: the pipeline only fires for non-trivial or cross-cutting requests. Trivial,
   obviously single-domain changes are still handled directly (or by a single worker subagent) with no
   overseer involvement — same as today.
4. **Approval gate**: the joint plan is shown to the user for approval before any worker subagent
   writes code, matching the existing `brainstorming → writing-plans` checkpoint discipline.
5. **Disagreement tripwire**: if the two overseers exchange more than 5 rounds on the same topic without
   resolving it, they stop and escalate that specific point to the user with both positions stated,
   rather than continuing to argue or one side silently deferring.

## Architecture

### Components

Two new custom subagent definitions, `.claude/agents/ui-lead.md` and `.claude/agents/backend-lead.md`.

- **`ui-lead`** — owns frontend scope. Reads `docs/agent/frontend.md` and `docs/agent/styling.md` (the
  same doc-ownership table `themis-planning` uses) to scope its half of the request.
- **`backend-lead`** — owns backend scope. Reads `docs/agent/backend.md`, `docs/agent/data-model.md`,
  and `docs/agent/printers.md` as relevant to the request.

Both definitions grant tool access to `Agent` (to spawn their own worker subagents),
`SendMessage` (to negotiate with each other), the normal read/edit/bash toolset, and instruct the
overseer to use `themis-planning` for scoping and `using-git-worktrees` for isolation. Each definition's
system prompt states the persona's domain boundary and instructs it to escalate rather than guess when
a decision crosses into the other domain (e.g. an API contract shape).

### Routing

The main assistant continues to triage every code-change request itself. A request only enters the
two-overseer pipeline when it is feature-sized or plausibly touches both `backend/` and `frontend/`, or
its shape is ambiguous. This is a judgment call, not a hard rule (e.g. not gated on line count) —
"trivial" isn't reliably measurable in advance. Trivial and single-domain requests bypass the overseers
entirely and are handled the way they are today.

### Negotiation protocol

1. The main assistant spawns both overseers in the same turn as named agents (`ui-lead`,
   `backend-lead`), each given the full request text and the other overseer's agent name.
2. Each overseer scopes its own side first, independently, via `themis-planning`.
3. The overseers then `SendMessage` each other to reconcile:
   - shared contracts (e.g. a new API field and the UI code that reads it),
   - sequencing (backend typically lands first when the UI depends on a new endpoint or shape),
   - file/ownership boundaries, to avoid both sides planning to touch the same file.
4. **Disagreement tripwire**: if a single topic goes past 5 message rounds between the two overseers
   without resolving, both stop negotiating that topic, and it is escalated back to the main assistant
   with each overseer's position stated. The main assistant brings it to the user rather than picking a
   side.
5. Once converged (or once all points are resolved, including any escalated ones), the overseers report
   one joint plan back to the main assistant: a file-level list per domain, shared contracts called out
   explicitly, and sequencing if one domain depends on the other landing first.

### Approval gate

The main assistant presents the joint plan to the user in the same file-level form `writing-plans`
already produces. Implementation does not start until the user approves it.

### Implementation

Once approved, both overseers dispatch their own worker subagents into one shared worktree/branch for
the change (via `using-git-worktrees`). Backend and frontend workers run in parallel — they mostly
touch disjoint directories — but commits are serialized (one commit at a time) to avoid git index lock
contention rather than attempting to parallelize git operations.

### Completion

Normal wrap-up applies unchanged: run the `CLAUDE.md` test/build commands, run `themis-docs-sync` if
`docs/agent/` drifted, then `finishing-a-development-branch` for the merge/PR decision.

### Edge cases

- **Disagreement tripwire** (see Decisions #5 and Negotiation protocol step 4).
- **Worker subagent failure**: its owning overseer retries once, then reports the failure up to the
  main assistant rather than leaving it silent or continuing as if it succeeded.
- **Both overseers plan to touch the same file**: caught during negotiation (step 3, file/ownership
  boundaries) before implementation starts, not during implementation.

## Not in scope

- No changes to the existing single-domain flow — trivial and single-domain requests are unaffected.
- No new persistent/always-running agent process; overseers are spawned per qualifying request, not
  kept alive across the session or across separate requests.
- No automatic merge-conflict resolution beyond serialized commits — a genuine conflict (e.g. both
  domains legitimately need to edit the same file) is a negotiation-time ownership question (see
  Negotiation protocol step 3), not something resolved after the fact.
