# jev_guide — Jev's manual, written by Jev, out of what Reddit said about it

Jev doesn't generate text. This example gets a guide to Jev out of it anyway: code pulls every
comment from the Reddit threads about Jev, splits them into sentences, Jev judges each one (is
it real advice, does it stand alone, is it first-hand, what's it about), code ranks them, and
Jev picks the best line per topic. That includes the topic on what Jev is bad at.

Every line in the output is a sentence a real person posted, linked to the comment it came
from. Nothing is summarized, so nothing can be made up.

The reference run is in [`results/`](results/).

## Run it

```bash
export REDDIT_CLIENT_ID=...       # free "script" app: https://www.reddit.com/prefs/apps
export REDDIT_CLIENT_SECRET=...
python3 examples/jev_guide/jev_guide.py --out guide.md
```

Reddit blocks anonymous JSON from most cloud IPs, so set the two variables unless you're on a
home connection. `TYPESAFE_API_KEY` comes from the kit's `.env` as usual.

| Flag | Default | |
|---|---|---|
| `--subs` | `ClaudeCode,ClaudeAI,LocalLLaMA,MachineLearning,accelerate,singularity` | where to look |
| `--window` | `month` | `day` `week` `month` `year` `all` |
| `--search` / `--match` | `jev OR typesafe` / `\bjev\b\|typesafe` | Reddit's search is fuzzy, so code keeps only threads that say the word |
| `--topics` | Jev topics | JSON `{id: description}`, must include `none` |
| `--audience` | someone building software with Jev | who the advice is for |
| `--per-topic` | `2` | lines picked per topic |
| `--threads` | | skip fetching, use a saved dump (any cache file works) |
| `--json-out` | | every scored sentence, for your own analysis |
| `--yes` | | skip the confirmation above $0.05 |

Fetched threads are cached in `~/.typesafe/reddit_cache/`, so re-running with different topics
costs only the Jev calls.

## Point it at something else

Nothing in the pipeline is Jev-specific except the defaults. A guide to any tool:

```bash
python3 examples/jev_guide/jev_guide.py --search "gliner" --match gliner \
  --subs LocalLLaMA,MachineLearning --topics gliner_topics.json \
  --audience "someone building with GLiNER" --title "The GLiNER guide"
```

Topic descriptions are the Choice criteria Jev reads, so describe situations, not single
words (`knowledge/MASTERY.md` has the reasoning), and always include a `none` option.

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
