# reddit_guide — a subreddit's guide, written by a model that can't write

Jev doesn't generate text. This example gets a guide out of it anyway: code splits every
comment on a subreddit's top posts into sentences, Jev judges each one (is it real advice,
does it stand alone, is it first-hand, what's it about), code ranks them, and Jev picks the
best line per topic. Every line in the output is a sentence a real person posted, linked to
the comment it came from. Nothing is summarized, so nothing can be made up.

The reference run is in [`results/2026-09-24-ClaudeCode.md`](results/2026-09-24-ClaudeCode.md):
7,728 sentences from the month's top 100 r/ClaudeCode posts, judged in 118 seconds for $0.21.

## Run it

```bash
export REDDIT_CLIENT_ID=...       # free "script" app: https://www.reddit.com/prefs/apps
export REDDIT_CLIENT_SECRET=...
python3 examples/reddit_guide/reddit_guide.py --sub ClaudeCode --out guide.md
```

Reddit blocks anonymous JSON from most cloud IPs, so set the two variables unless you're on a
home connection. `TYPESAFE_API_KEY` comes from the kit's `.env` as usual.

| Flag | Default | |
|---|---|---|
| `--sub` | `ClaudeCode` | any subreddit |
| `--posts` | `100` | top posts to read (max 100) |
| `--window` | `month` | `day` `week` `month` `year` `all` |
| `--topics` | Claude Code topics | JSON `{id: description}`, must include `none` |
| `--per-topic` | `2` | lines picked per topic |
| `--threads` | | skip fetching, use a saved dump (any cache file works) |
| `--json-out` | | every scored sentence, for your own analysis |
| `--yes` | | skip the confirmation above $0.05 |

Fetched threads are cached in `~/.typesafe/reddit_cache/`, so re-running with different topics
or weights costs only the Jev calls.

## Point it at another subreddit

The default topics are about Claude Code. For anything else, write your own:

```json
{
  "hardware": "choosing, buying, or configuring hardware",
  "networking": "reverse proxies, DNS, VPNs, or exposing services safely",
  "backups": "backup strategy, restore testing, or data loss",
  "none": "not advice about self-hosting"
}
```

```bash
python3 examples/reddit_guide/reddit_guide.py --sub selfhosted --topics selfhosted.json
```

Topic descriptions are the Choice criteria Jev reads, so describe situations, not single
words (`knowledge/MASTERY.md` has the reasoning).

## What it shows

- **Select, don't generate.** Code finds candidates, the model only chooses. The same shape
  works for release notes, support macros, FAQ answers, search results.
- **Policy stays in code.** The ranking weights (`FIRSTHAND_BONUS`, `MIN_TOPIC_CONF`,
  `POOL_SIZE`) are constants at the top of the file, not words in a prompt. Tuning them
  never touches the questions Jev is asked.
- **Fan-out is cheap.** One request per sentence, 16 in flight, about $0.03 per thousand
  sentences.

## What it doesn't show

Whether Jev's taste is any good. The topic labels and picks are unvalidated judgments; the
output says so. Reading the guide is the check. To put a number on it, hand-label a few
hundred sentences (`--json-out` gives you the file to label), register the sentence questions
as a QuestionSet (see CONTRIBUTING.md), and run `tools/confidence_accuracy_curve.py` on `tip`.
