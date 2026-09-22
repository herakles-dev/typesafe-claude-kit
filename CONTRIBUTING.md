# Contributing to typesafe-claude-kit

## Adding a QuestionSet

1. Drop a module in `lib/questions/your_surface.py`. It is auto-discovered — nothing else has
   to import it by name.
2. Build a `QuestionSet` (see `lib/questions/__init__.py` docstring) and call `register(...)` on
   it at import time, the way `lib/questions/ticket_triage.py` does.
3. Name every threshold — never an inline literal — and leave `validated_on=None` until it has
   actually been measured against labelled data. Set `gates_irreversible=True` for anything that
   gates a hard-to-undo action; `.ask()` then refuses to run until `validated_on` is filled in.
4. Add a live test in `tests/` (see `tests/test_client.py` or `tests/test_questions.py` for the
   pattern: no pytest, a `check(name, condition)` counter, `sys.exit()` on failure).

## Adding an agent

Keep the mandate sharp and non-overlapping with the existing six — overlap is how handoffs get
dropped. A new agent file in `agents/` needs Claude Code agent frontmatter: `name`,
`description`, `model`, `color`, `category`, `default_mode`, `triggers`, and
`handoff_from`/`handoff_to` where relevant. Give it a **Home base** section naming its route
into this repo, explicit **Boundaries**, and an **Output** contract. See any existing file in
`agents/` as a template, and update `agents/README.md`'s table.

## Before opening a PR

Run the tests:

```bash
python3 tests/run_all.py
```

Most suites make live calls against the TypeSafe API and need `TYPESAFE_API_KEY` set — they are
cheap (well under a cent per suite) but not free. `python3 tests/run_all.py --list` shows what
will run without spending anything.

## Secrets

`.env` is gitignored. Never commit an API key, and never put one in a commit message, an issue,
or a test fixture. Use `.env.example` to document new variables, not `.env` itself.

## Attribution

Docstrings, comments, and `knowledge/*.md` content that distill or quote TypeSafe's own
documentation (docs.typesafe.ai) should say so, the way the existing `knowledge/*.md` files do
in their second line. Don't present upstream material as original analysis.

## Using Claude Code

This repo is built to be worked on with Claude Code. `CLAUDE.md` and `AGENTS.md` give it the
context it needs; the six agents in `agents/` are the specialists for TypeSafe-shaped work.

```bash
claude    # reads CLAUDE.md automatically
```
