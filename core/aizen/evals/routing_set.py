"""Loader for `evals/routing_set.yaml` (Part II §32 question set)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field

from aizen.domains import Domain


class EvalMeta(BaseModel):
    num_repeats: int = 3
    temperature: float = 0.1


class EvalQuestion(BaseModel):
    id: str
    text: str
    domain: Domain
    expected: str | None = None
    expect_tool: bool = False
    no_data: bool = False
    stub: dict[str, dict[str, object]] = Field(default_factory=dict)
    expected_toolset_names: list[str] | None = None
    note: str = ""


class RoutingEvalSet(BaseModel):
    meta: EvalMeta = EvalMeta()
    questions: list[EvalQuestion]

    @classmethod
    def load(cls, path: Path) -> Self:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            meta=EvalMeta(**raw.get("meta", {})),
            questions=[EvalQuestion(**q) for q in raw.get("questions", [])],
        )

    @property
    def no_data_ids(self) -> set[str]:
        return {q.id for q in self.questions if q.no_data}

    def question(self, question_id: str) -> EvalQuestion | None:
        return next((q for q in self.questions if q.id == question_id), None)


__all__ = ["EvalMeta", "EvalQuestion", "RoutingEvalSet"]