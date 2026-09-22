"""Question-set convention for TypeSafe integrations.

A `QuestionSet` bundles the three things that must travel together and must not drift apart:

  1. the questions,
  2. the named thresholds that read their answers,
  3. the record of what data those thresholds were validated against.

Thresholds are the part that quietly rots. The docs are blunt that cookbook numbers are
"examples to evaluate, not universal rules", and that a threshold tuned on one question type
does not carry to another. So a threshold here is never an inline magic number: it is named,
documented, and carries provenance.

The hard rule this module enforces mechanically: **a question set that gates an irreversible
action may not run until its thresholds have been validated on real data.** Set
`gates_irreversible=True` and `validated_on=None` and `.ask()` raises rather than acting on an
unvalidated cutoff.

Writing a set:

    # lib/questions/my_surface.py
    from . import QuestionSet, register
    from typesafe_client import choice, noul

    MY_SET = QuestionSet(
        name="my_surface",
        version="1.0.0",
        description="One line on what decision this drives.",
        questions={...},
        thresholds={"ACT_ABOVE": 0.85},
        gates_irreversible=True,
        validated_on=None,       # -> .ask() refuses until this is filled in
    )
    register(MY_SET)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typesafe_client import DEFAULT_MODEL, Result, TypeSafeClient  # noqa: E402

__all__ = ["QuestionSet", "UnvalidatedThresholds", "register", "get", "names", "all_sets"]


class UnvalidatedThresholds(RuntimeError):
    """Raised when a set that gates an irreversible action has unvalidated thresholds."""


@dataclass(frozen=True)
class QuestionSet:
    """A reusable, versioned bundle of questions plus the policy that reads them."""

    name: str
    version: str
    description: str
    questions: Mapping[str, Mapping[str, Any]]
    thresholds: Mapping[str, float] = field(default_factory=dict)

    #: Pin a version. An alias moves on release and can shift answers under a tuned cutoff.
    model: str = DEFAULT_MODEL

    #: True when acting on these answers is hard to undo (deleting a record, shipping code,
    #: sending mail). Forces the validation check below.
    gates_irreversible: bool = False

    #: What the thresholds were tuned against, e.g. "142 hand-labelled probes,
    #: 2026-09-19". None means "not yet validated".
    validated_on: str | None = None

    #: The function that builds this set's state -- for example, something that turns a raw
    #: support-ticket record into `ticket_triage`'s `{"ticket": ...}` shape. Optional, but set
    #: it: it turns a convention into a check. When present, `.ask()` rejects a state carrying
    #: top-level keys the builder never produces.
    #:
    #: This exists because the convention alone failed once: a labelled file recorded each case
    #: under a field named `state` that was not the state -- it carried extra top-level keys the
    #: builder never produces and the model never reads. Passing it straight through cost
    #: measurable accuracy (irrelevant state degrades an answer -- failure mode #5), and
    #: separately one of those extra fields held free-form text the model read as an
    #: instruction rather than data. Both were top-level keys, so this check catches both.
    state_builder: Any = None

    #: Top-level keys the builder produces. Derived once, lazily, on first `.ask()`.
    _allowed_keys: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError(f"QuestionSet {self.name!r} has no questions")
        if self.model.endswith("latest") or self.model.endswith("preview"):
            if self.thresholds:
                raise ValueError(
                    f"QuestionSet {self.name!r} pairs thresholds with the moving alias "
                    f"{self.model!r}. Pin a version so a release cannot shift answers "
                    "underneath a tuned cutoff."
                )

    @property
    def is_validated(self) -> bool:
        return bool(self.validated_on)

    def threshold(self, key: str) -> float:
        """Look up a named threshold. Fails loudly rather than defaulting."""
        try:
            return self.thresholds[key]
        except KeyError:
            raise KeyError(
                f"{self.name!r} has no threshold {key!r}. Defined: {sorted(self.thresholds)}"
            ) from None

    def ask(
        self,
        client: TypeSafeClient,
        state: Any,
        *,
        allow_unvalidated: bool = False,
    ) -> Result:
        """Evaluate this set's questions against `state`.

        Refuses to run an irreversible-gating set whose thresholds are unvalidated, unless
        `allow_unvalidated=True` -- which is how you run it during the validation pass itself.
        """
        if self.gates_irreversible and not self.is_validated and not allow_unvalidated:
            raise UnvalidatedThresholds(
                f"QuestionSet {self.name!r} v{self.version} gates an irreversible action but "
                "its thresholds have not been validated. Run the validation pass with "
                "allow_unvalidated=True, then record the result in `validated_on`."
            )
        self._check_state_shape(state)
        return client.ask(
            state=state,
            questions=self.questions,
            model=self.model,
            tag=f"{self.name}@{self.version}",
        )

    def _check_state_shape(self, state: Any) -> None:
        """Reject a state carrying top-level keys this set's builder never produces.

        A no-op unless `state_builder` is set. The allowed key set is taken from the first
        state the builder is asked to produce, using empty inputs -- builders here read only
        the keys they forward, so an empty call yields the right shape with empty values.
        If the builder cannot run that way, the check disables itself rather than guessing.
        """
        if self.state_builder is None or not isinstance(state, dict):
            return
        if self._allowed_keys is None:
            try:
                probe = self.state_builder({}, {})
                object.__setattr__(self, "_allowed_keys", frozenset(probe))
            except Exception:  # noqa: BLE001 -- an un-probeable builder just disables the check
                object.__setattr__(self, "_allowed_keys", frozenset())
        if not self._allowed_keys:
            return
        extra = set(state) - set(self._allowed_keys)
        if extra:
            raise ValueError(
                f"QuestionSet {self.name!r} received state with top-level key(s) "
                f"{sorted(extra)} that {self.state_builder.__name__}() does not produce. "
                f"Expected only {sorted(self._allowed_keys)}. Build test inputs through the "
                "builder rather than passing a raw record -- unread state costs accuracy "
                "(failure mode #5) and has already produced two false defects here."
            )


_REGISTRY: dict[str, QuestionSet] = {}


def register(question_set: QuestionSet) -> QuestionSet:
    """Add a set to the registry. Re-registering the same name is an error."""
    existing = _REGISTRY.get(question_set.name)
    if existing is not None and existing.version != question_set.version:
        raise ValueError(
            f"{question_set.name!r} already registered at v{existing.version}; "
            f"refusing to shadow with v{question_set.version}"
        )
    _REGISTRY[question_set.name] = question_set
    return question_set


def get(name: str) -> QuestionSet:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"No question set {name!r}. Registered: {names()}") from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def all_sets() -> list[QuestionSet]:
    return [_REGISTRY[n] for n in names()]


# --- Auto-discovery -------------------------------------------------------------------------
#
# A module dropped in this directory registers itself by calling `register(...)` at import
# time (see the module docstring above). Nothing previously triggered that import -- a caller
# doing `from questions import get` had no way to see a set unless something, somewhere, had
# already imported its module by name. Import every sibling module once, here, so `get()` and
# `names()` see whatever lives in this directory without the caller needing to know which file
# defines which set.


def _autodiscover() -> None:
    import importlib
    import pkgutil

    package_dir = Path(__file__).resolve().parent
    log = logging.getLogger("typesafe.questions")
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{module_info.name}")
        except Exception as exc:  # noqa: BLE001 -- one bad module must not break discovery
            log.warning("Could not auto-import questions.%s: %s", module_info.name, exc)


_autodiscover()
