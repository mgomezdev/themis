# Linear sync

How an agent keeps a Linear issue's status in step with the work, via the Linear MCP (`save_issue`). Applies when the request or
the branch name carries a tracker ID (`BIZ-NN`). Status names are the team's (Biz); there is no "In Progress" or "In Review".

## Statuses and what they mean

| Status (type) | Meaning | Expected actor (a convention; nothing enforces it) |
|---|---|---|
| Backlog (backlog), Todo (unstarted) | Not started, or parked (blocked / paused) | Human triage; the agent moves an issue back here when blocked |
| Research (started) | Scoping against `docs/agent/`, unknowns being resolved | Agent |
| Design (started) | Brainstorming and writing the plan in `docs/superpowers/plans/` | Agent |
| Test-first (started) | Failing tests written before the change | Agent |
| Develop (started) | Implementing; the suites and the reviewer pass (`CLAUDE.md` PR gate) happen here | Agent |
| Confirmation testing (started) | PR open; CI running or being fixed, awaiting merge (the reviewer pass and local suites come first, under *Develop*) | Agent |
| **Human review** (started) | **A human must look.** See below | Agent sets it (for the reasons below); the human clears it |
| Done (completed) | Merged into `develop` | Agent for ordinary issues; the human for epics |
| Canceled / Duplicate | Abandoned, or covered by another issue | Agent says why in a comment |

Normally move forward and skip a state the work did not need (a one-line fix goes Develop → Confirmation testing → Done). The only moves back are resuming after Human review and the blocked / abandoned rule below.

**When work starts:** non-trivial work begins in Research (scoping against `docs/agent/`) or Design (the plan), then Test-first if tests are written ahead of the change; a small, well-understood change goes straight to Develop. Link the PR on the
issue when it is opened.

## Human review is for exactly two things

1. **Closing an epic.** An epic is a parent issue with sub-issues (titled "Epic: …" by convention) that spans many issues and large changes; Linear Projects have no issue status and are out of scope. An agent never moves an epic to Done. When every child is Done
   (or Canceled with a reason) and the epic's acceptance criteria are met, set the epic to **Human review** and comment with: what was
   delivered, what was canceled or deferred and why, and any accepted gaps. The human closes it.
2. **Needing the human on an ordinary issue.** Ordinary issues do **not** pass through Human review on the way to Done. Set it only when the agent
   (a) needs confirmation of the work (a behaviour or UX call it cannot verify, a manual check it cannot run) or (b) took many
   assumptions to get there. Leave a comment first, written so the human can act without reading the code:
   * **Asking for:** one line (confirm / decide / review the assumptions).
   * **Assumptions:** a numbered list, each with what was assumed and what changes if it is wrong.
   * **Where:** branch / PR / commit.

   Then stop work on that issue. This state is the human's trigger to open the issue and act; once they respond, the agent
   resumes from the status that fits (usually Develop).

One or two minor assumptions go in the PR description. Escalate when an assumption could change the outcome the owner asked for, or when there are roughly three or more.

## Rules

* Move to **Develop** when implementation starts, to **Confirmation testing** when the PR is opened (link it), to **Done** once merged into `develop`.
* Blocked: set Todo (still wanted) or Backlog (parked) and comment why. Dropped: Canceled with the reason, or Duplicate with a link to the other issue.
* Done once the PR is merged into `develop`; the agent learns of the merge from the PR (a subscribed PR event, or a check when asked).
* Policy, not a Linear rule: do not close a parent while children are open. Finish, cancel (with a reason) or unparent them first.
* If the issue has no matching workflow state, skip the update and say so; never guess a state.
