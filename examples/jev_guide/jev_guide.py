#!/usr/bin/env python3
"""jev_guide -- Jev writes its own manual out of what Reddit said about it, without writing a word.

Jev cannot write. This script gets a guide to Jev out of it anyway, by never asking it to:

  1. Code searches Reddit for Jev threads and pulls every comment under them.
  2. Code splits the comments into sentences and drops the obvious non-candidates
     (questions, links, fragments).
  3. Jev judges every sentence in one request each: is it actionable advice for someone
     building with Jev, does it stand on its own, is it first-hand, and which topic is it about.
  4. Code ranks candidates per topic from those judgments and keeps a small pool.
  5. Jev picks the best line from each pool with a Choice question -- twice, so every topic
     gets a runner-up. That includes the topic about what Jev is bad at.

Every line in the output is a sentence a real person posted, linked to its comment. Nothing is
summarized, so nothing can be made up. This is extraction by selection
(`knowledge/cookbooks-extraction.md`): code finds the candidates, the model only chooses.

What it does NOT tell you: whether Jev's taste is any good. The picks are unvalidated
judgments. Read them -- that is the check.

CLI:
    python3 examples/jev_guide/jev_guide.py --out guide.md
    python3 examples/jev_guide/jev_guide.py --subs LocalLLaMA --window week
    python3 examples/jev_guide/jev_guide.py --search "gliner" --match gliner \\
        --topics gliner_topics.json --audience "someone building with GLiNER" --title "The GLiNER guide"

Fetching needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (a free "script" app at
https://www.reddit.com/prefs/apps) on most networks: Reddit blocks anonymous JSON from cloud
IPs. Without them the script tries the anonymous endpoint and says so if it is blocked.

`--topics` is a JSON object of {id: description}. It must include "none". Descriptions are the
Choice criteria, so write them as situations, not single words.

The reference run is in `results/`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

KIT = Path(os.environ.get("TYPESAFE_KIT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(KIT / "lib"))
from typesafe_client import USD_PER_INPUT_TOKEN, TypeSafeClient, choice, noul  # noqa: E402

USER_AGENT = "typesafe-claude-kit/jev_guide (github.com/herakles-dev/typesafe-claude-kit)"

DEFAULT_SEARCH = "jev OR typesafe"
DEFAULT_MATCH = r"\bjev\b|typesafe"  # search is fuzzy; code keeps only threads that say the word
DEFAULT_SUBS = "ClaudeCode,ClaudeAI,LocalLLaMA,MachineLearning,accelerate,singularity"
DEFAULT_AUDIENCE = "someone building software with Jev"
DEFAULT_TITLE = "The Jev guide"
DEFAULT_TOPICS = {
    "good_for": "what Jev is good at, or a use case where it worked",
    "bad_at": "what Jev is bad at, gets wrong, or should not be used for",
    "questions": "how to write questions for Jev: wording, options, criteria, or Score levels",
    "confidence": "trusting Jev's probabilities or confidence: calibration, thresholds, checking it",
    "cost": "Jev's price, speed, batching, or rate limits",
    "building": "wiring Jev into code, an agent, or Claude Code: the API, SDKs, or architecture",
    "compare": "how Jev compares to LLMs, fine-tuned classifiers like BERT or GLiNER, or local models",
    "none": "not advice about using Jev",
}

# Candidate filter. Deliberately code, not model: length and punctuation are lookups.
MIN_CHARS, MAX_CHARS, MIN_WORDS = 50, 260, 8
MIN_COMMENTS = 3        # threads with fewer comments rarely hold advice
POOL_SIZE = 15          # candidates per topic handed to the final Choice
MIN_TOPIC_CONF = 0.6    # below this, the topic label is a coin flip; leave the sentence out
FIRSTHAND_BONUS = 0.15  # policy, not inference: prefer "I did X" over "you should X"


# --- fetch ---------------------------------------------------------------------------------

def _session() -> tuple[requests.Session, str]:
    """App-only OAuth when REDDIT_CLIENT_ID/SECRET are set, else the anonymous public JSON.

    Reddit blocks anonymous JSON from most cloud and datacenter IPs (403 "Blocked"), and is
    tightening it everywhere. A free "script" app at https://www.reddit.com/prefs/apps gives
    you a client id and secret; read-only app-only auth needs nothing else.
    """
    session = requests.Session()
    session.headers["User-Agent"] = os.environ.get("REDDIT_USER_AGENT", USER_AGENT)
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return session, "https://www.reddit.com"
    r = session.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                     data={"grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    session.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return session, "https://oauth.reddit.com"


def _get(session: requests.Session, url: str, params: dict) -> dict | list:
    for attempt in range(5):
        r = session.get(url, params={**params, "raw_json": 1}, timeout=30)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 403 and "Authorization" not in session.headers:
            sys.exit("Reddit blocked the anonymous request (403). Set REDDIT_CLIENT_ID and "
                     "REDDIT_CLIENT_SECRET from a free script app at "
                     "https://www.reddit.com/prefs/apps, or pass --threads with a saved dump.")
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Reddit kept rate-limiting {url}")


def _walk(children: list, out: list) -> None:
    for child in children:
        if child.get("kind") != "t1":
            continue  # "more" stubs: skipped, the top of each thread is plenty
        c = child["data"]
        out.append({"id": c["id"], "body": c.get("body", ""), "score": c.get("score", 0),
                    "author": c.get("author", "")})
        replies = c.get("replies")
        if isinstance(replies, dict):
            _walk(replies["data"]["children"], out)


def fetch(search: str, match: str, subs: list[str], window: str, cache: Path) -> list[dict]:
    """Matching threads from each sub plus their comment trees, cached so re-runs are free."""
    cache.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"\W+", "_", f"{search}-{'_'.join(subs)}-{window}")[:120]
    path = cache / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())

    session, base = _session()
    wanted = re.compile(match, re.I)
    posts: dict[str, dict] = {}
    for sub in subs:
        listing = _get(session, f"{base}/r/{sub}/search",
                       {"q": search, "restrict_sr": 1, "sort": "top", "t": window,
                        "limit": 100, "type": "link"})
        for child in listing["data"]["children"]:
            p = child["data"]
            if (p.get("num_comments", 0) >= MIN_COMMENTS
                    and wanted.search(p["title"] + " " + (p.get("selftext") or ""))):
                posts[p["id"]] = p
        time.sleep(1)

    threads = []
    for p in posts.values():
        tree = _get(session, f"{base}/comments/{p['id']}",
                    {"limit": 500, "depth": 4, "sort": "top"})
        comments: list[dict] = []
        _walk(tree[1]["data"]["children"], comments)
        threads.append({"id": p["id"], "title": p["title"], "score": p["score"],
                        "sub": p["subreddit"], "permalink": "https://www.reddit.com" + p["permalink"],
                        "comments": comments})
        time.sleep(1)  # well inside Reddit's 100 requests/minute for OAuth clients
    path.write_text(json.dumps(threads))
    return threads


# --- candidates ----------------------------------------------------------------------------

def sentences(threads: list[dict]) -> list[dict]:
    out = []
    for t in threads:
        for c in t["comments"]:
            if c["author"] in ("AutoModerator", "[deleted]") or c["body"] in ("[deleted]", "[removed]"):
                continue
            body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c["body"])  # keep link text only
            for line in body.split("\n"):
                if line.strip().startswith(">"):
                    continue  # quoting someone else
                for s in re.split(r"(?<=[.!])\s+", line):
                    s = s.strip(" *-#\t")
                    if (MIN_CHARS <= len(s) <= MAX_CHARS and not s.endswith("?")
                            and "http" not in s and len(s.split()) >= MIN_WORDS):
                        out.append({"s": s, "cid": c["id"], "cscore": c["score"],
                                    "thread": t["title"], "sub": t.get("sub", ""),
                                    "link": f"{t['permalink']}{c['id']}/", "tscore": t["score"]})
    return out


# --- judgments -----------------------------------------------------------------------------

def sentence_questions(topics: dict, audience: str) -> dict:
    return {
        "tip": noul(f"This sentence gives specific, actionable advice or a warning that "
                    f"{audience} could apply today, rather than an opinion, a joke, a "
                    f"complaint, or a vague statement."),
        "standalone": noul("This sentence makes complete sense on its own, without the rest "
                           "of the comment or thread."),
        "firsthand": noul("The author is describing something they actually did themselves "
                          "and what happened."),
        "topic": choice("What is this sentence about?", topics),
    }


def score_all(client: TypeSafeClient, cands: list[dict], topics: dict, audience: str,
              workers: int) -> tuple[list[dict], float]:
    questions = sentence_questions(topics, audience)
    spent = [0.0]

    def one(x: dict) -> dict:
        try:
            r = client.ask(state={"thread_title": x["thread"], "sentence": x["s"]},
                           questions=questions, tag="jev-guide")
        except Exception as e:  # one bad call should not sink a 7,000-call run
            return {**x, "err": str(e)[:200]}
        spent[0] += r.cost_usd
        return {**x, "tip": r.noul("tip"), "standalone": r.noul("standalone"),
                "firsthand": r.noul("firsthand"), "topic": r.choice("topic"),
                "tconf": r.confidence("topic")}

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, cands))
    return rows, spent[0]


def pick(client: TypeSafeClient, rows: list[dict], topics: dict, audience: str,
         per_topic: int) -> tuple[dict, float]:
    """Rank in code, choose with Jev. The weights are constants, not words in a prompt."""
    picks, spent = {}, 0.0
    for tid, desc in topics.items():
        if tid == "none":
            continue
        cand = [r for r in rows if "err" not in r and r["topic"] == tid and r["tconf"] >= MIN_TOPIC_CONF]
        cand.sort(key=lambda r: -(r["tip"] * r["standalone"] + FIRSTHAND_BONUS * r["firsthand"]))
        pool, seen = [], set()
        for r in cand:  # one sentence per comment, so one chatty commenter can't fill the pool
            if r["cid"] not in seen:
                seen.add(r["cid"])
                pool.append(r)
            if len(pool) == POOL_SIZE:
                break
        chosen: list[dict] = []
        taken: set[int] = set()
        for _ in range(per_topic):
            opts = {f"s{i}": r["s"] for i, r in enumerate(pool) if i not in taken}
            if len(opts) < 2:
                break
            res = client.ask(
                state={"reader": audience},
                questions={"best": choice(f"Which sentence is the single most useful piece of "
                                          f"advice about {desc}?", opts)},
                tag="jev-guide-pick")
            spent += res.cost_usd
            i = int(res.choice("best")[1:])
            taken.add(i)
            chosen.append({**pool[i], "pick_conf": res.confidence("best")})
        picks[tid] = chosen
    return picks, spent


# --- output --------------------------------------------------------------------------------

def render(title: str, picks: dict, topics: dict, rows: list[dict], threads: int,
           seconds: float, cost: float) -> str:
    ok = [r for r in rows if "err" not in r]
    counts = {t: sum(r["topic"] == t for r in ok) for t in topics}
    kept = [p for ps in picks.values() for p in ps]
    lines = [f"# {title}, written by a model that can't write", "",
             f"{len(ok):,} sentences from {threads} Reddit threads, judged in {seconds:.0f} s "
             f"for ${cost:.2f}. Every line below is a real comment; Jev only chose.", ""]
    for tid, ps in picks.items():
        if not ps:
            continue
        lines += [f"## {topics[tid][0].upper()}{topics[tid][1:]}", ""]
        for p in ps:
            where = f"r/{p['sub']}, " if p.get("sub") else ""
            lines.append(f"> {p['s']}")
            lines.append(f">\n> [{where}{p['cscore']} upvotes, in a thread with {p['tscore']}]({p['link']})")
            lines.append("")
    if kept:
        thread_scores = {p["link"].rsplit("/", 2)[0]: p["tscore"] for p in kept}
        lines += ["## The numbers", "",
                  f"- Upvotes on the comments quoted above: {sum(p['cscore'] for p in kept)} total.",
                  f"- Upvotes on the threads they came from: {sum(thread_scores.values())} total.",
                  "- Sentences per topic: " + ", ".join(f"{t} {n}" for t, n in
                                                          sorted(counts.items(), key=lambda kv: -kv[1])),
                  "", "Topic labels and picks are Jev's unvalidated judgments.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--search", default=DEFAULT_SEARCH, help="Reddit search query")
    ap.add_argument("--match", default=DEFAULT_MATCH,
                    help="regex a thread's title or body must match (search results are fuzzy)")
    ap.add_argument("--subs", default=DEFAULT_SUBS, help="comma-separated subreddits")
    ap.add_argument("--window", default="month", choices=["day", "week", "month", "year", "all"])
    ap.add_argument("--topics", help="JSON file of {id: description}; must include 'none'")
    ap.add_argument("--audience", default=DEFAULT_AUDIENCE, help="who the advice is for")
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--per-topic", type=int, default=2)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--cache", default=str(Path.home() / ".typesafe" / "reddit_cache"))
    ap.add_argument("--threads", help="skip fetching: a saved dump in this script's thread format "
                    "(a cache file from an earlier run works)")
    ap.add_argument("--out", help="write the guide here (default: stdout)")
    ap.add_argument("--json-out", help="also dump every scored sentence here")
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = ap.parse_args()

    topics = json.loads(Path(args.topics).read_text()) if args.topics else DEFAULT_TOPICS
    if "none" not in topics:
        sys.exit("--topics must include a 'none' option, or every sentence gets forced into a topic")

    threads = (json.loads(Path(args.threads).read_text()) if args.threads
               else fetch(args.search, args.match, [s.strip() for s in args.subs.split(",")],
                          args.window, Path(args.cache)))
    cands = sentences(threads)
    est = len(cands) * 630 * USD_PER_INPUT_TOKEN  # ~630 input tokens per sentence call, measured
    print(f"{len(threads)} threads, {len(cands):,} candidate sentences, ~${est:.2f} estimated",
          file=sys.stderr)
    if est > 0.05 and not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        return 1

    client = TypeSafeClient()
    t0 = time.monotonic()
    rows, cost = score_all(client, cands, topics, args.audience, args.workers)
    picks, pick_cost = pick(client, rows, topics, args.audience, args.per_topic)
    seconds = time.monotonic() - t0

    guide = render(args.title, picks, topics, rows, len(threads), seconds, cost + pick_cost)
    if args.out:
        Path(args.out).write_text(guide)
    else:
        print(guide)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows))
    errs = sum("err" in r for r in rows)
    print(f"done: {len(rows) - errs:,} scored, {errs} errors, {seconds:.0f} s, "
          f"${cost + pick_cost:.3f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
