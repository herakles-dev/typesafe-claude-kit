# r/ClaudeCode, written by a model that can't write

_Reference run, 2026-09-24: every comment on the month's top 100 posts. Upvote counts are as of that day._

7,728 sentences judged in 118 s for $0.21. Every line below is a real comment; Jev only chose.

## CLAUDE.md, memory files, or standing project instructions

> You can put the general orchestration rules in ~/.claude/CLAUDE.md so they apply across repos.
>
> [2 upvotes, in a thread with 385](https://www.reddit.com/r/ClaudeCode/comments/1wbc03f/how_i_use_subagents_without_burning_through_fable/p8rpwip/)

> In practice, a short `CLAUDE.md` with natural language rules beats a 400-line harness.
>
> [1 upvotes, in a thread with 416](https://www.reddit.com/r/ClaudeCode/comments/1wk2q8v/agentsmd_now_supported_in_claude_code/paq0lta/)

## managing context: compaction, clearing, long sessions, what Claude remembers

> If you want to cut per-turn overhead further, the levers that matter more are keeping long tool outputs out of the transcript, using subagents for file sweeps so dumps stay out of your context, and `/clear` between unrelated tasks.
>
> [1 upvotes, in a thread with 1237](https://www.reddit.com/r/ClaudeCode/comments/1w2ja43/tip_instantly_save_10k_tokens_on_every_new_session/p7byvvn/)

> I have Claude Code remind me to /clear at a 200k context window and to write out a handoff brief that is automatically picked up through a hook after the clear.
>
> [1 upvotes, in a thread with 353](https://www.reddit.com/r/ClaudeCode/comments/1wde783/how_are_people_burning_through_their_fable_tokens/p96djou/)

## usage limits, cost, tokens, or choosing a model or plan

> And if you go into the billing tab there should be an estimated date when you're going to run out if you keep at this pace
>
> [10 upvotes, in a thread with 336](https://www.reddit.com/r/ClaudeCode/comments/1wl5mbn/82_of_my_what/paxnwcr/)

> The question is never "is this task hard?" It's "can I mechanically check the output?" If a cheaper model's work can be verified with a grep, a diff, or a test run, send it down the ladder.
>
> [3 upvotes, in a thread with 590](https://www.reddit.com/r/ClaudeCode/comments/1wf9uuf/wth_is_going_on_with_claude_usage_limits/p9k8c2h/)

## keeping Claude from breaking code: tests, reviews, verifying its work, git

> i’d rather have it inspect the project first, figure out how those modules are connected, then make the changes and verify that it didn’t break anything else.
>
> [3 upvotes, in a thread with 2095](https://www.reddit.com/r/ClaudeCode/comments/1w4qziv/ok_this_is_wild_used_claude_fable_51_and_said/p7chqpc/)

> After that, I review the diff myself locally in my IDE.
>
> [8 upvotes, in a thread with 670](https://www.reddit.com/r/ClaudeCode/comments/1wgm4si/engineers_who_write_all_their_code_with_claude/p9vilu6/)

## subagents, parallel work, worktrees, or orchestrating several agents

> Tiny, surgical task + exact files + clear limits = useful subagent.
>
> [3 upvotes, in a thread with 385](https://www.reddit.com/r/ClaudeCode/comments/1wbc03f/how_i_use_subagents_without_burning_through_fable/p8ru1nd/)

> I highly recommend adopting worktree workflows so that you can launch separate areas of work async on the same project.
>
> [53 upvotes, in a thread with 670](https://www.reddit.com/r/ClaudeCode/comments/1wgm4si/engineers_who_write_all_their_code_with_claude/p9vfslo/)

## permissions, hooks, dangerous commands, or security

> I’d move the safety test into a disposable worktree or container, deny writes outside the repo, and use a harmless canary file.
>
> [1 upvotes, in a thread with 436](https://www.reddit.com/r/ClaudeCode/comments/1wb1wrg/claude_just_tried_to_test_if_a_new_permission/p8zakfw/)

> "permissions": { "deny": ["Bash(sudo date:*)", "Bash(sudo systemsetup:*)"] }
>
> [17 upvotes, in a thread with 530](https://www.reddit.com/r/ClaudeCode/comments/1w6w7y1/claude_recommended_that_i_set_my_macs_date_to_the/p7qclx4/)

## how to phrase requests, plan mode, or giving specs and examples

> i usually explain what i want the feature to do, the constraints around it, and what “done” should look like.
>
> [3 upvotes, in a thread with 2095](https://www.reddit.com/r/ClaudeCode/comments/1w4qziv/ok_this_is_wild_used_claude_fable_51_and_said/p7chqpc/)

> I prompt it with "minimal blast radius" so it doesn't rewrite the whole code base while fixing one bug
>
> [2 upvotes, in a thread with 1488](https://www.reddit.com/r/ClaudeCode/comments/1wnt4d3/they_fucking_cooked_yo_opus_55_is_a_massive/pbicwqb/)

## a general daily habit for working with Claude Code

> I have Claude come back to me at the end of each discrete task, I walk through the diff, commit the changes by coming up with my own message that's human readable for my colleagues, then go to the next discrete task.
>
> [2 upvotes, in a thread with 361](https://www.reddit.com/r/ClaudeCode/comments/1w6yw16/claude_code_v21259_forces_coauthoredby/p7sqlj4/)

> You give the direction, goals, and sometimes high level architectural guidance and then focus on automating the processes that will steer the AI back on track when it strays.
>
> [103 upvotes, in a thread with 670](https://www.reddit.com/r/ClaudeCode/comments/1wgm4si/engineers_who_write_all_their_code_with_claude/p9vqjvf/)

## The numbers

- Upvotes on the comments quoted above: 213 total.
- Upvotes on the threads they came from: 8897 total.
- Sentences per topic: none 3147, limits 2127, quality 472, agents 463, workflow 461, prompting 428, context 325, safety 200, claude_md 105

Topic labels and picks are Jev's unvalidated judgments.
