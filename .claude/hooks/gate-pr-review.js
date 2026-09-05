#!/usr/bin/env node
// PreToolUse gate: blocks `gh pr create` / mcp__github__create_pull_request
// unless .claude/review-state.json records a clean review for current HEAD.
// See CLAUDE.md "Development workflow" for the process this enforces.
'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function deny(reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }));
  process.exit(0);
}

function allow() {
  process.exit(0);
}

let payload = {};
try {
  payload = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
} catch {
  allow(); // malformed/absent stdin - don't block on a hook-plumbing issue
}

const toolName = payload.tool_name || '';
const command = (payload.tool_input && payload.tool_input.command) || '';

// Anchored to a command-position match (start of string, or right after a shell
// separator/operator) so the phrase merely appearing in prose - e.g. inside a
// heredoc commit message that talks about this very hook - doesn't false-positive.
const isGhPrCreate = toolName === 'Bash' &&
  /(^|[;&|\n]|&&|\|\|)\s*gh\s+pr\s+create\b/.test(command);
const isMcpCreatePr = toolName === 'mcp__github__create_pull_request';

if (!isGhPrCreate && !isMcpCreatePr) {
  allow();
}

let gitRoot;
let headSha;
try {
  gitRoot = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
  headSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: gitRoot, encoding: 'utf8' }).trim();
} catch {
  allow(); // not a git repo / git unavailable - don't block on infra issues
}

const markerPath = path.join(gitRoot, '.claude', 'review-state.json');

let marker = null;
try {
  marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
} catch {
  marker = null; // missing or unreadable marker = no valid review on file
}

if (marker && marker.sha === headSha && marker.verdict === 'clean') {
  allow();
}

deny(
  `No clean review recorded for HEAD (${headSha}). Before creating this PR: ` +
  `1) Check .claude/review-state.json - if it already matches this sha with verdict "clean" this block is a bug, re-check the sha. ` +
  `2) Otherwise dispatch exactly one fresh, non-fork reviewer subagent per CLAUDE.md's Development workflow section, ` +
  `handing it the base/head SHAs and pointing it at docs/agent/backend-review.md and/or docs/agent/frontend-review.md as applicable. ` +
  `3) Address any Critical/Important findings (fix now, or new commits then re-review). ` +
  `4) Write .claude/review-state.json as {"sha":"${headSha}","verdict":"clean","reviewed_at":"<iso8601>"}. ` +
  `5) Retry creating the PR - this check will pass without spawning another review.`
);
