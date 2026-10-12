---
name: review-loop
description: Review → fix → re-test loop for this repo. Finds bugs in a target (default the uncommitted diff vs master), hands confirmed-worthy findings to the bug-fixer subagent to prove with a failing test, fix, and re-run the validation gates, then re-reviews the result. Use when the user asks for a review loop, to "review and fix", or runs /review-loop.
argument-hint: "[target: diff (default) | <path> | <commit-range>] [--rounds N] [--worktree]"
disable-model-invocation: true
---

# /review-loop

You (the main session) orchestrate; the `bug-fixer` subagent does the fixing. Subagents can't
spawn subagents, so the loop lives here.

## 1. Resolve the target

From the arguments (default `diff`):
- `diff` — uncommitted changes vs HEAD plus untracked source files: `git diff HEAD` and
  `git status --porcelain`. Ignore `.claude/`, `misc/`, `output/`, `logs/`, `venv/`.
- `<path>` — review that file/directory as a whole.
- `<commit-range>` (e.g. `master~3..master`) — `git diff <range>`.

If the `diff` target is empty (work already committed), fall back to the last commit
(`HEAD~1..HEAD`) and tell the user you did.

**Isolation.** Use `isolation: "worktree"` for the subagent only when `--worktree` is passed
**and** the target is already committed (a worktree is cut from HEAD, so it won't contain
uncommitted changes). Otherwise the subagent works in place — tell the user that it will.

## 2. Review (find)

Review the target for **correctness bugs** — logic errors, unhandled edge cases, broken
invariants, security issues — not style. Run the built-in code-review skill at medium effort
(`/code-review medium` for an uncommitted diff, `/code-review medium <path>` otherwise — it
takes paths, not commit ranges; given a path it reviews recent changes to that file plus the
file itself). **Also review it yourself in parallel**: code-review stays inside the target
files, so trace data across boundaries it won't — e.g. values that end up rendered in
`templates/index.html`, or rows written by `summaries/extract.py` and served by the web UI.

Give extra scrutiny to this repo's risk areas when they're touched:
- `storage/sanitizer.py` — HTML/script stripping, the injection scanner's coverage;
- dedup on `(message_id, label)` and DB migration in `storage/database.py`;
- IMAP `mark_read` / `BODY.PEEK` semantics in `access/imap.py`;
- FTS query sanitization in `summaries/db.py`;
- request parsing and data exposure in `webserver.py`.

Verify each candidate against the code before passing it on — drop anything you can't point
to concretely. Each finding needs: file:line, the defect in one sentence, and a concrete
failure scenario (inputs/state → wrong result). If nothing survives, report "no findings"
and stop.

## 3. Fix (delegate)

Merge both finding lists, dedupe (fold findings that share one root cause into one), drop
cosmetic/perf items, and keep the top 5 by severity — list the rest in the final report as
deferred. Spawn the `bug-fixer` agent (`subagent_type: "bug-fixer"`) with the numbered findings
(file:line, defect, failure scenario, a fix hint and a test hint each) and the target
description. If that agent type isn't registered (agents added mid-session aren't), spawn
`general-purpose` with `model: "sonnet"` and tell it to first read
`.claude/agents/bug-fixer.md` and follow it as its operating instructions.

## 4. Re-review (one more round)

After the fixer returns, review **only its changes** (`git diff` of the files it touched) for
regressions or bad fixes — weakened tests, over-broad changes, fixes that don't address the
stated scenario. New findings → run step 3 again with just those. Stop after `--rounds`
rounds (default **2**) or when a round produces no new findings.

## 5. Independently re-run the gates

Don't take the subagent's word for it — run the full suite yourself:
`venv/bin/python -m unittest discover -s tests`.

## 6. Report to the user

A short table: finding → status (fixed / not-reproduced / needs-human / dismissed in
re-review) → test added → files. Then the final gate results, anything `needs-human` with
the reason, and a reminder that changes are uncommitted for them to review
(`git diff`). Don't commit.
