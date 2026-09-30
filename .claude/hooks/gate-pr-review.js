#!/usr/bin/env node
// PreToolUse gate: blocks `gh pr create` / mcp__github__create_pull_request
// unless .claude/review-state.json records a clean review for current HEAD with the test/coverage
// suites confirmed (`checks`: "pass" or "n/a").
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

function allow(diagnostic) {
  // Silent on the common "doesn't match" path (this runs on every Bash/PowerShell
  // call) - only surface a message when allowing despite an actual anomaly, so
  // the gate's occasional fail-open is visible in the transcript instead of
  // vanishing with zero signal.
  if (diagnostic) process.stderr.write(`gate-pr-review: allowing - ${diagnostic}\n`);
  process.exit(0);
}

let payload = {};
try {
  payload = JSON.parse(fs.readFileSync(0, 'utf8') || '{}') || {};
} catch {
  allow('malformed/absent stdin'); // don't block on a hook-plumbing issue
}

const toolName = payload.tool_name || '';
const command = (payload.tool_input && payload.tool_input.command) || '';

// Best-effort: drop the contents of single/double-quoted spans before matching,
// so a separator or the trigger phrase appearing inside a quoted argument (e.g.
// a commit message) doesn't read as command position. Not shell-accurate
// (doesn't handle escaped quotes or heredocs) - this is a cooperative-model
// safety net, not an adversarial sandbox, so it prioritizes cutting false
// positives over catching every disguised invocation.
function stripQuotedSpans(cmd) {
  return cmd.replace(/'[^']*'|"[^"]*"/g, ' ');
}

// Anchored to a command-position match (start of string, or right after a shell
// separator/operator) so the phrase merely appearing in prose - e.g. inside a
// heredoc commit message that talks about this very hook - doesn't false-positive.
// Covers both Bash (this repo's Bash tool) and PowerShell (its primary shell) -
// both are separate tools with their own `command` field.
const isGhPrCreate = (toolName === 'Bash' || toolName === 'PowerShell') &&
  /(^|[;&|\n]|&&|\|\|)\s*gh\s+pr\s+create\b/.test(stripQuotedSpans(command));
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
  allow('git unavailable or not a git repo'); // don't block on infra issues
}

const markerPath = path.join(gitRoot, '.claude', 'review-state.json');

let marker = null;
try {
  marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
} catch {
  marker = null; // missing or unreadable marker = no valid review on file
}

// `checks` records that the suites in CLAUDE.md > Commands were run green at this sha ("pass"), or
// that nothing they cover changed ("n/a", e.g. a docs-only diff). A review without it says nothing about
// whether the tests it leans on actually pass.
const CHECKS_OK = new Set(['pass', 'n/a']);

if (marker && marker.sha === headSha && marker.verdict === 'clean' && CHECKS_OK.has(marker.checks)) {
  allow();
}

const reviewed = marker && marker.sha === headSha && marker.verdict === 'clean';

// A clean review already on file for this sha only needs the suites confirmed - don't send the agent
// off to dispatch a second reviewer.
const steps = [
  `Check .claude/review-state.json - if it already matches this sha with verdict "clean" and checks "pass" (or "n/a") this block is a bug, re-check the sha.`,
  `Run the suites for what the diff touches (CLAUDE.md > Commands): backend \`pytest -v -ra --cov\` from backend/ (enforces the coverage floor), ` +
    `frontend \`npm run build\` + \`npm run test:cov\`, \`npm run test:e2e\` if a screen or e2e mock changed; ` +
    `regenerate openapi.json (\`python scripts/export_openapi.py\`, from the repo root) if routes or params changed. ` +
    `All must pass - never skip or loosen a test or floor to get there.`,
];
if (!reviewed) {
  steps.push(
    `Dispatch exactly one fresh, non-fork reviewer subagent per CLAUDE.md's Development workflow section, ` +
    `handing it the base/head SHAs and pointing it at docs/agent/backend-review.md and/or docs/agent/frontend-review.md as applicable ` +
    `(they cover reviewing the tests themselves, not just the product code).`,
    `Address any Critical/Important findings (fix now, or new commits then re-run the suites and the review).`,
  );
}
steps.push(
  `Write .claude/review-state.json as {"sha":"${headSha}","verdict":"clean","checks":"pass","reviewed_at":"<iso8601>"} ` +
    `("n/a" for checks only if the diff touches nothing the suites cover).`,
  `Retry creating the PR - this check will pass without spawning another review.`,
);

deny(
  (reviewed
    ? `The review for HEAD (${headSha}) is recorded clean, but the marker's checks is ${marker.checks === undefined ? 'missing' : JSON.stringify(marker.checks)}, not "pass" or "n/a". `
    : `No clean review recorded for HEAD (${headSha}). `) +
  `This applies to any way of opening the PR ` +
  `(gh pr create, the GitHub MCP tool, or a raw \`gh api ... pulls\` call) - do not route around it. ` +
  `Before creating this PR: ` +
  steps.map((step, i) => `${i + 1}) ${step}`).join(' ')
);
