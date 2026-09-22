"""Client for the TypeSafe System One API (Jev).

Thin wrapper over `POST /v1/systemone`. Deliberately built on `requests` rather than the
official `typesafe-sdk` so any service can import it without a dependency change.

Design rules, each traceable to the docs (see knowledge/MASTERY.md):

- **Model is pinned, not aliased.** `jev-latest` moves on release and answers can change
  underneath a tuned threshold. Callers who genuinely want the moving alias must pass it.
- **Validation happens locally.** Documented limits (Choice <=255 options, Score 2-10 levels)
  are checked before the request so failures surface as ValueError at the call site rather
  than a 422 from the server.
- **Retries honor the documented semantics.** 429 and 529 are retryable with exponential
  backoff; `Retry-After` wins when present. Nothing else is retried.
- **Usage is logged.** Every call appends a record so spend and latency stay observable.

Usage:

    from typesafe_client import TypeSafeClient, choice, noul, score

    client = TypeSafeClient()
    result = client.ask(
        state={"ticket": "My card was charged twice."},
        questions={
            "refund": noul("Does `ticket` request a refund?"),
            "team": choice("Which team handles this?",
                           {"billing": "Charges and refunds",
                            "support": "Everything else"}),
        },
    )
    result.noul("refund")            # -> 0.93
    result.choice("team")            # -> "billing"
    result.confidence("team")        # -> 0.88
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

__all__ = [
    "TypeSafeClient",
    "TypeSafeError",
    "Result",
    "choice",
    "score",
    "noul",
    "estimate_tokens",
]

log = logging.getLogger("typesafe")

# --- Documented constants (models.md, primitives/*.md, api.md) -----------------------------

API_URL = "https://api.typesafe.ai/v1/systemone"
MODELS_URL = "https://api.typesafe.ai/v1/models"

#: Pinned by default. `jev-latest` is an alias that moves on release; the docs are explicit
#: that tuned thresholds should pin a version and migrate deliberately.
DEFAULT_MODEL = "jev-1.13.0"

MAX_CHOICE_OPTIONS = 255          # primitives/choice.md
MIN_SCORE_LEVELS = 2              # api.md: "A Score should have at least two levels; the API accepts up to 10."
MAX_SCORE_LEVELS = 10             # primitives/score.md: "up to 10"
TOTAL_TOKEN_BUDGET = 64_000       # models.md
STATE_PLUS_QUESTION_BUDGET = 32_000

#: $42 per billion input tokens. Output tokens are free.
USD_PER_INPUT_TOKEN = 42 / 1e9

RETRYABLE_STATUS = {429, 529}

DEFAULT_USAGE_LOG = Path(
    os.environ.get("TYPESAFE_USAGE_LOG", str(Path.home() / ".typesafe" / "usage.jsonl"))
)


class TypeSafeError(RuntimeError):
    """An API call failed in a way that is not worth retrying."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


# --- Question constructors ----------------------------------------------------------------
#
# These exist to validate at construction time. Each returns the plain dict the API expects,
# so a caller who prefers raw dicts loses nothing.


def choice(instructions: Any, criteria: Mapping[str, Any]) -> dict:
    """A Choice question: select one option from a defined set.

    `criteria` maps option name -> description. A description may be None when the option
    name speaks for itself, or a dict/list for contrastive definitions (what / not_for /
    examples), which the docs recommend when two options keep getting confused.
    """
    if not criteria:
        raise ValueError("Choice requires at least one option")
    if len(criteria) > MAX_CHOICE_OPTIONS:
        raise ValueError(
            f"Choice accepts at most {MAX_CHOICE_OPTIONS} options, got {len(criteria)}"
        )
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: Any, criteria: Iterable[Any]) -> dict:
    """A Score question: a position along ordered, described levels (low -> high).

    Levels must describe concrete situations. Bare numbers or degree words ("moderate")
    measurably degrade the answer -- see MASTERY.md section 3.
    """
    levels = list(criteria)
    if not (MIN_SCORE_LEVELS <= len(levels) <= MAX_SCORE_LEVELS):
        raise ValueError(
            f"Score requires {MIN_SCORE_LEVELS}-{MAX_SCORE_LEVELS} levels, got {len(levels)}"
        )
    for i, level in enumerate(levels):
        if isinstance(level, str) and re.fullmatch(r"\s*\d+\s*", level):
            raise ValueError(
                f"Score level {i} is the bare number {level!r}. Levels must describe a "
                "situation the model can match the state against."
            )
    return {"type": "score", "instructions": instructions, "criteria": levels}


def noul(instructions: Any, criteria: Mapping[str, Any] | None = None) -> dict:
    """A Noul question: the probability that a yes/no condition holds.

    Phrase it so that a high value means "yes". Note there is no confidence field on a Noul
    answer, and 0.5 means "yes and no are equally likely", not "medium intensity".
    """
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria is not None:
        unknown = set(criteria) - {"true", "false"}
        if unknown:
            raise ValueError(f"Noul criteria accepts only 'true'/'false', got {sorted(unknown)}")
        q["criteria"] = dict(criteria)
    return q


def estimate_tokens(payload: Any) -> int:
    """Rough token estimate for budget warnings.

    Approximates at 4 characters per token. This is a guardrail for the 64k/32k budgets, not
    an accounting figure -- the response's `usage.input_tokens` is authoritative.
    """
    return len(json.dumps(payload, ensure_ascii=False)) // 4


# --- Result -------------------------------------------------------------------------------


@dataclass
class Result:
    """A System One response, with accessors that fail loudly on type mismatch."""

    model: str
    answers: dict[str, dict]
    usage: dict[str, int]
    latency_ms: int
    raw: dict = field(repr=False, default_factory=dict)

    def _answer(self, qid: str, expected: str) -> dict:
        try:
            answer = self.answers[qid]
        except KeyError:
            raise KeyError(
                f"No answer for question {qid!r}. Present: {sorted(self.answers)}"
            ) from None
        if answer.get("type") != expected:
            raise TypeError(
                f"Question {qid!r} is a {answer.get('type')!r}, not a {expected!r}"
            )
        return answer

    def noul(self, qid: str) -> float:
        """P(yes) for a Noul question."""
        return float(self._answer(qid, "noul")["noul"])

    def choice(self, qid: str) -> str:
        """The selected option for a Choice question."""
        return str(self._answer(qid, "choice")["choice"])

    def score(self, qid: str) -> float:
        """The probability-weighted position for a Score question."""
        return float(self._answer(qid, "score")["score"])

    def normalized_score(self, qid: str) -> float:
        """A Score mapped onto 0-1 by dividing by its top level number.

        Required before combining Scores with different level counts -- a 4-level scale tops
        out at 3 and a 3-level scale at 2, so raw scores are not comparable.
        """
        answer = self._answer(qid, "score")
        top = len(answer["legend"]) - 1
        if top <= 0:
            raise ValueError(f"Question {qid!r} has too few levels to normalize")
        return float(answer["score"]) / top

    def confidence(self, qid: str) -> float:
        """Confidence for a Choice or Score answer. Nouls do not carry one."""
        answer = self.answers[qid]
        if "confidence" not in answer:
            raise TypeError(
                f"Question {qid!r} is a {answer.get('type')!r}; only Choice and Score "
                "answers carry a confidence value."
            )
        return float(answer["confidence"])

    def probabilities(self, qid: str) -> dict[str, float]:
        """The full distribution. Read this when `score` alone is ambiguous."""
        answer = self.answers[qid]
        if "probabilities" not in answer:
            raise TypeError(f"Question {qid!r} has no probability distribution")
        return {k: float(v) for k, v in answer["probabilities"].items()}

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("input_tokens", 0))

    @property
    def cost_usd(self) -> float:
        """Input tokens only -- output tokens are free."""
        return self.input_tokens * USD_PER_INPUT_TOKEN


# --- Client -------------------------------------------------------------------------------


class TypeSafeClient:
    """Calls the System One endpoint with retries, validation, and usage logging."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
        max_retries: int = 4,
        usage_log: Path | None = DEFAULT_USAGE_LOG,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or _load_api_key()
        if not self.api_key:
            raise TypeSafeError(
                "No TypeSafe API key. Set TYPESAFE_API_KEY (or put it in .env at the kit root)."
            )
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage_log = Path(usage_log) if usage_log else None
        self._session = session or requests.Session()

    # -- public API ------------------------------------------------------------------------

    def ask(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        model: str | None = None,
        tag: str | None = None,
    ) -> Result:
        """Evaluate `state` against `questions` in a single request.

        Send every question the calling code might need, including speculative ones. They are
        evaluated in parallel and cost only their own tokens; a second round trip costs far
        more than a question whose answer goes unused.

        `tag` is recorded in the usage log so spend can be attributed to a call site.
        """
        if not questions:
            raise ValueError("At least one question is required")

        payload = {
            "state": state,
            "model": model or self.model,
            "questions": dict(questions),
        }
        self._warn_on_budget(payload)

        started = time.monotonic()
        body = self._post_with_retries(payload)
        latency_ms = int((time.monotonic() - started) * 1000)

        result = Result(
            model=body.get("model", ""),
            answers=body.get("answers", {}),
            usage=body.get("usage", {}),
            latency_ms=latency_ms,
            raw=body,
        )
        self._log_usage(result, question_ids=list(questions), tag=tag)
        return result

    def list_models(self) -> list[dict]:
        """`GET /v1/models` -- the names this account may send in the `model` field."""
        response = self._session.get(
            MODELS_URL, headers=self._headers(), timeout=self.timeout
        )
        if response.status_code != 200:
            raise TypeSafeError(
                f"Listing models failed with {response.status_code}",
                status=response.status_code,
                body=response.text[:500],
            )
        return response.json().get("models", [])

    # -- internals -------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _warn_on_budget(self, payload: dict) -> None:
        total = estimate_tokens(payload)
        if total > TOTAL_TOKEN_BUDGET:
            log.warning(
                "Estimated %d tokens exceeds the %d budget; the request will likely 422.",
                total,
                TOTAL_TOKEN_BUDGET,
            )
            return
        longest = max(
            (estimate_tokens(q) for q in payload["questions"].values()), default=0
        )
        if estimate_tokens(payload["state"]) + longest > STATE_PLUS_QUESTION_BUDGET:
            log.warning(
                "state plus the longest question is near the %d budget.",
                STATE_PLUS_QUESTION_BUDGET,
            )

    def _post_with_retries(self, payload: dict) -> dict:
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.post(
                    API_URL, headers=self._headers(), json=payload, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                self._sleep(attempt, None)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._sleep(attempt, response.headers.get("Retry-After"))
                continue

            # 401 / 422 and anything else: not worth retrying, and the body names the problem.
            raise TypeSafeError(
                f"TypeSafe returned {response.status_code}",
                status=response.status_code,
                body=response.text[:1000],
            )

        raise TypeSafeError(
            f"TypeSafe unreachable after {self.max_retries + 1} attempts: {last_error}"
        )

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None) -> None:
        """Exponential backoff with jitter; `Retry-After` wins when the server sends one."""
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 60.0))
                return
            except (TypeError, ValueError):
                pass
        time.sleep(min(2**attempt + random.uniform(0, 0.5), 30.0))

    def _log_usage(self, result: Result, *, question_ids: list[str], tag: str | None) -> None:
        if not self.usage_log:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tag": tag,
            "model": result.model,
            "questions": len(question_ids),
            "question_ids": question_ids,
            "input_tokens": result.input_tokens,
            "cost_usd": round(result.cost_usd, 9),
            "latency_ms": result.latency_ms,
        }
        try:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError as exc:  # logging must never break a caller
            log.debug("Could not write usage log: %s", exc)


# --- Key loading --------------------------------------------------------------------------


def _load_api_key() -> str | None:
    """TYPESAFE_API_KEY from the environment, falling back to a `.env` file at the kit root.

    The `.env` fallback is a minimal KEY=VALUE parser (no new dependency): blank lines and
    lines starting with `#` are ignored, and a value may be wrapped in matching quotes.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key.strip()

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "TYPESAFE_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None
