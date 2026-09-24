#!/usr/bin/env python3
"""reddit_guide -- write a subreddit's field guide out of its own comments, without generating a word.

Jev cannot write. This script makes it write a guide anyway, by never asking it to:

  1. Code fetches the month's top posts and every comment under them (Reddit's public JSON).
  2. Code splits the comments into sentences and drops the obvious non-candidates
     (questions, links, fragments).
  3. Jev judges every sentence in one request each: is it actionable advice, does it stand
     on its own, is it first-hand, and which topic is it about.
  4. Code ranks candidates per topic from those judgments and keeps a small pool.
  5. Jev picks the best line from each pool with a Choice question -- twice, so every topic
     gets a runner-up.

Every line in the output is a sentence a real person posted, linked to its comment. Nothing is
summarized, so nothing can be made up. This is extraction by selection
(`knowledge/cookbooks-extraction.md`): code finds the candidates, the model only chooses.

What it does NOT tell you: whether Jev's taste is any good. The picks are unvalidated
judgments. Read them -- that is the check -- and if you want numbers, label some sentences and
run `tools/confidence_accuracy_curve.py` against them.

CLI:
    python3 examples/reddit_guide/reddit_guide.py --sub ClaudeCode
    python3 examples/reddit_guide/reddit_guide.py --sub ClaudeCode --posts 25 --out guide.md
    python3 examples/reddit_guide/reddit_guide.py --sub selfhosted --topics my_topics.json

Fetching needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (a free "script" app at
https://www.reddit.com/prefs/apps) on most networks: Reddit now blocks anonymous JSON from
cloud IPs. Without them the script tries the anonymous endpoint and says so if it is blocked.

`--topics` is a JSON object of {id: description}. It must include "none". Descriptions are
the Choice criteria, so write them as situations, not single words.

Reference run (r/ClaudeCode, top 100 of the month, 2026-09-24): 7,728 sentences scored in
118 s at 16 workers for $0.21. The result is in `results/`.
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

USER_AGENT = "typesafe-claude-kit/reddit_guide (github.com/herakles-dev/typesafe-claude-kit)"

DEFAULT_TOPICS = {
    "claude_md": "CLAUDE.md, memory files, or standing project instructions",
    "context": "managing context: compaction, clearing, long sessions, what Claude remembers",
    "limits": "usage limits, cost, tokens, or choosing a model or plan",
    "quality": "keeping Claude from breaking code: tests, reviews, verifying its work, git",
    "agents": "subagents, parallel work, worktrees, or orchestrating several agents",
    "safety": "permissions, hooks, dangerous commands, or security",
    "prompting": "how to phrase requests, plan mode, or giving specs and examples",
    "workflow": "a general daily habit for working with Claude Code",
    "none": "not advice about using Claude Code",
}

# Candidate filter. Deliberately code, not model: length and punctuation are lookups.
MIN_CHARS, MAX_CHARS, MIN_WORDS = 50, 260, 8
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


def fetch(sub: str, posts: int, window: str, cache: Path) -> list[dict]:
    """Top posts for the window plus their comment trees, cached on disk so re-runs are free."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{sub}-{window}-{posts}.json"
    if path.exists():
        return json.loads(path.read_text())

    session, base = _session()
    listing = _get(session, f"{base}/r/{sub}/top", {"t": window, "limit": min(posts, 100)})
    threads = []
    for child in listing["data"]["children"][:posts]:
        p = child["data"]
        if p.get("num_comments", 0) < 5:
            continue
        tree = _get(session, f"{base}/comments/{p['id']}",
                    {"limit": 500, "depth": 4, "sort": "top"})
        comments: list[dict] = []
        _walk(tree[1]["data"]["children"], comments)
        threads.append({"id": p["id"], "title": p["title"], "score": p["score"],
                        "permalink": "https://www.reddit.com" + p["permalink"],
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
                                    "thread": t["title"], "link": f"{t['permalink']}{c['id']}/",
                                    "tscore": t["score"]})
    return out


# --- judgments -----------------------------------------------------------------------------

def sentence_questions(topics: dict) -> dict:
    return {
        "tip": noul("This sentence gives specific, actionable advice that a Claude Code user "
                    "could apply today, rather than an opinion, a joke, a complaint, or a vague "
                    "statement."),
        "standalone": noul("This sentence makes complete sense on its own, without the rest "
                           "of the comment or thread."),
        "firsthand": noul("The author is describing something they actually did themselves "
                          "and what happened."),
        "topic": choice("What is this sentence about?", topics),
    }


def score_all(client: TypeSafeClient, cands: list[dict], topics: dict, workers: int) -> tuple[list[dict], float]:
    questions = sentence_questions(topics)
    spent = [0.0]

    def one(x: dict) -> dict:
        try:
            r = client.ask(state={"thread_title": x["thread"], "sentence": x["s"]},
                           questions=questions, tag="reddit-guide")
        except Exception as e:  # one bad call should not sink a 7,000-call run
            return {**x, "err": str(e)[:200]}
        spent[0] += r.cost_usd
        return {**x, "tip": r.noul("tip"), "standalone": r.noul("standalone"),
                "firsthand": r.noul("firsthand"), "topic": r.choice("topic"),
                "tconf": r.confidence("topic")}

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, cands))
    return rows, spent[0]


def pick(client: TypeSafeClient, rows: list[dict], topics: dict, per_topic: int) -> dict:
    """Rank in code, choose with Jev. Weights live here so changing them needs no re-inference."""
    picks = {}
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
        for _ in range(per_topic):
            opts = {f"s{i}": r["s"] for i, r in enumerate(pool) if r not in chosen}
            if len(opts) < 2:
                break
            res = client.ask(
                state={"reader": "a developer who uses Claude Code every day"},
                questions={"best": choice(f"Which sentence is the single most useful piece of "
                                          f"advice about {desc}?", opts)},
                tag="reddit-guide-pick")
            winner = pool[int(res.choice("best")[1:])]
            chosen.append({**winner, "pick_conf": res.confidence("best")})
        picks[tid] = chosen
    return picks


# --- output --------------------------------------------------------------------------------

def render(sub: str, picks: dict, topics: dict, rows: list[dict], seconds: float, cost: float) -> str:
    ok = [r for r in rows if "err" not in r]
    counts = {t: sum(r["topic"] == t for r in ok) for t in topics}
    kept = [p for ps in picks.values() for p in ps]
    lines = [f"# r/{sub}, written by a model that can't write", "",
             f"{len(ok):,} sentences judged in {seconds:.0f} s for ${cost:.2f}. "
             f"Every line below is a real comment; Jev only chose.", ""]
    for tid, ps in picks.items():
        if not ps:
            continue
        lines += [f"## {topics[tid]}", ""]
        for p in ps:
            lines.append(f"> {p['s']}")
            lines.append(f">\n> [{p['cscore']} upvotes, in a thread with {p['tscore']}]({p['link']})")
            lines.append("")
    if kept:
        lines += ["## The numbers", "",
                  f"- Upvotes on the comments quoted above: {sum(p['cscore'] for p in kept)} total.",
                  f"- Upvotes on the threads they came from: {sum({p['link'].rsplit('/', 2)[0]: p['tscore'] for p in kept}.values())} total.",
                  "- Sentences per topic: " + ", ".join(f"{t} {n}" for t, n in
                                                          sorted(counts.items(), key=lambda kv: -kv[1])),
                  "", "Topic labels and picks are Jev's unvalidated judgments.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sub", default="ClaudeCode")
    ap.add_argument("--posts", type=int, default=100, help="top posts to read (max 100)")
    ap.add_argument("--window", default="month", choices=["day", "week", "month", "year", "all"])
    ap.add_argument("--topics", help="JSON file of {id: description}; must include 'none'")
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
               else fetch(args.sub, args.posts, args.window, Path(args.cache)))
    cands = sentences(threads)
    est = len(cands) * 630 * USD_PER_INPUT_TOKEN  # ~630 input tokens per sentence call, measured
    print(f"{len(threads)} threads, {len(cands):,} candidate sentences, ~${est:.2f} estimated",
          file=sys.stderr)
    if est > 0.05 and not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        return 1

    client = TypeSafeClient()
    t0 = time.monotonic()
    rows, cost = score_all(client, cands, topics, args.workers)
    picks = pick(client, rows, topics, args.per_topic)
    seconds = time.monotonic() - t0

    guide = render(args.sub, picks, topics, rows, seconds, cost)
    if args.out:
        Path(args.out).write_text(guide)
    else:
        print(guide)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows))
    errs = sum("err" in r for r in rows)
    print(f"done: {len(rows) - errs:,} scored, {errs} errors, {seconds:.0f} s, ${cost:.3f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
