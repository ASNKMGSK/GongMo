# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Scorer Skill -- rule-based scoring engine for QA evaluation items.

Converts LLM or rule-based verdicts into numeric scores according to the
deduction rules defined in ``nodes.qa_rules.QA_RULES``.

All 18 items, 8 categories, and the 100-point total are covered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nodes.qa_rules import QA_RULES, get_rule_by_item_number


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ScoreResult:
    """Score result for a single QA evaluation item."""

    item_number: int
    item_name: str
    max_score: int
    score: int
    reason: str
    evidence_turns: list[int] = field(default_factory=list)
    confidence: float = 0.85


@dataclass
class CategoryResult:
    """Aggregated score for one QA category."""

    category: str
    category_en: str
    max_score: int
    score: int
    items: list[ScoreResult] = field(default_factory=list)


@dataclass
class TotalResult:
    """Aggregated score across all categories (100-point scale)."""

    max_score: int
    score: int
    categories: list[CategoryResult] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Category map -- derived from QA_RULES at import time
# ---------------------------------------------------------------------------

# Build a lookup: category_name -> {category, category_en, items: [item_number, ...], max_score}
_CATEGORY_MAP: dict[str, dict[str, Any]] = {}
for _rule in QA_RULES:
    _cat = _rule["category"]
    if _cat not in _CATEGORY_MAP:
        _CATEGORY_MAP[_cat] = {
            "category": _cat,
            "category_en": _rule["category_en"],
            "items": [],
            "max_score": 0,
        }
    _CATEGORY_MAP[_cat]["items"].append(_rule["item_number"])
    _CATEGORY_MAP[_cat]["max_score"] += _rule["max_score"]


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class Scorer:
    """Rule-based scoring engine backed by ``qa_rules.QA_RULES``."""

    # Expose the category map for callers that need the structure.
    CATEGORY_MAP = _CATEGORY_MAP

    # -----------------------------------------------------------------
    # score_item
    # -----------------------------------------------------------------

    @staticmethod
    def score_item(
        item_number: int,
        verdict: str | int,
        deductions: list[dict[str, Any]] | None = None,
        *,
        evidence_turns: list[int] | None = None,
        reason: str = "",
        confidence: float = 0.85,
    ) -> ScoreResult:
        """Convert a verdict into a ``ScoreResult`` for the given item.

        Parameters
        ----------
        item_number:
            QA item number (1-18).
        verdict:
            ``"full"`` for max score, ``"partial"`` for middle tier,
            ``"fail"`` for zero, or an explicit ``int`` score.
        deductions:
            Optional list of deduction dicts -- used only to enrich *reason*
            when *reason* is not provided.
        evidence_turns:
            Turn IDs that support the verdict.
        reason:
            Human-readable reason string.
        confidence:
            Confidence of the verdict (0.0 - 1.0).
        """
        rule = get_rule_by_item_number(item_number)
        if rule is None:
            return ScoreResult(
                item_number=item_number,
                item_name=f"Unknown item #{item_number}",
                max_score=0,
                score=0,
                reason=f"No rule found for item {item_number}",
                evidence_turns=evidence_turns or [],
                confidence=0.0,
            )

        max_score = rule["max_score"]
        deduction_rules = rule.get("deduction_rules", [])

        # Resolve score from verdict.
        if isinstance(verdict, int):
            score = max(0, min(verdict, max_score))
        elif verdict == "full":
            score = max_score
        elif verdict == "partial":
            # Pick the middle tier from deduction_rules.
            if deduction_rules:
                score = deduction_rules[0].get("to_score", 0)
            else:
                score = 0
        else:  # "fail" or anything else
            score = 0

        # Build reason if not provided.
        if not reason and deductions:
            parts = [d.get("reason", "") for d in deductions if d.get("reason")]
            reason = "; ".join(parts) if parts else ""
        if not reason:
            if score == max_score:
                reason = rule["full_score_criteria"]
            elif score == 0:
                # Use the harshest deduction rule's condition.
                if deduction_rules:
                    reason = deduction_rules[-1].get("condition", "감점")
                else:
                    reason = "감점"
            else:
                # Partial -- find matching deduction rule.
                for dr in deduction_rules:
                    if dr.get("to_score") == score:
                        reason = dr.get("condition", "부분 감점")
                        break
                else:
                    reason = "부분 감점"

        return ScoreResult(
            item_number=item_number,
            item_name=rule["name"],
            max_score=max_score,
            score=score,
            reason=reason,
            evidence_turns=evidence_turns or [],
            confidence=confidence,
        )

    # -----------------------------------------------------------------
    # score_category
    # -----------------------------------------------------------------

    @staticmethod
    def score_category(
        category: str,
        items: list[ScoreResult],
    ) -> CategoryResult:
        """Aggregate item scores into a ``CategoryResult``.

        Parameters
        ----------
        category:
            Korean category name (e.g. "인사 예절").  Must exist in
            ``_CATEGORY_MAP``.
        items:
            ``ScoreResult`` instances belonging to this category.
        """
        cat_info = _CATEGORY_MAP.get(category)
        if cat_info is None:
            return CategoryResult(
                category=category,
                category_en="unknown",
                max_score=sum(i.max_score for i in items),
                score=sum(i.score for i in items),
                items=items,
            )

        return CategoryResult(
            category=cat_info["category"],
            category_en=cat_info["category_en"],
            max_score=cat_info["max_score"],
            score=sum(i.score for i in items),
            items=items,
        )

    # -----------------------------------------------------------------
    # score_total
    # -----------------------------------------------------------------

    @staticmethod
    def score_total(
        all_categories: list[CategoryResult],
        *,
        flags: list[str] | None = None,
    ) -> TotalResult:
        """Aggregate all category scores into a 100-point ``TotalResult``.

        Parameters
        ----------
        all_categories:
            All ``CategoryResult`` instances (one per category).
        flags:
            Optional list of flag strings (e.g. "privacy_violation",
            "unkind_zero_point").
        """
        total_max = sum(c.max_score for c in all_categories)
        total_score = sum(c.score for c in all_categories)

        return TotalResult(
            max_score=total_max,
            score=total_score,
            categories=all_categories,
            flags=flags or [],
        )

    # -----------------------------------------------------------------
    # Convenience: build a full report from flat evaluation dicts
    # -----------------------------------------------------------------

    @classmethod
    def build_report(
        cls,
        evaluations: list[dict[str, Any]],
        *,
        flags: list[str] | None = None,
    ) -> TotalResult:
        """Build a ``TotalResult`` from the flat evaluation dicts produced by
        the LangGraph nodes.

        Each element in *evaluations* is expected to have at minimum
        ``evaluation.item_number``, ``evaluation.score``, and
        ``evaluation.max_score``.
        """
        # Collect ScoreResults keyed by item_number.
        item_results: dict[int, ScoreResult] = {}
        for ev in evaluations:
            ed = ev.get("evaluation", {})
            item_num = ed.get("item_number")
            if item_num is None:
                continue
            rule = get_rule_by_item_number(item_num)
            item_name = rule["name"] if rule else f"Item #{item_num}"
            max_score = ed.get("max_score", rule["max_score"] if rule else 0)

            # Build reason from deductions if available.
            deductions = ed.get("deductions", [])
            reason_parts = [d.get("reason", "") for d in deductions if d.get("reason")]
            reason = "; ".join(reason_parts) if reason_parts else ""

            evidence = ed.get("evidence", [])
            ev_turns = [e.get("turn", 0) for e in evidence if isinstance(e, dict) and "turn" in e]

            item_results[item_num] = ScoreResult(
                item_number=item_num,
                item_name=item_name,
                max_score=max_score,
                score=ed.get("score", 0),
                reason=reason,
                evidence_turns=ev_turns,
                confidence=ed.get("confidence", 0.85),
            )

        # Group by category.
        category_results: list[CategoryResult] = []
        for cat_name, cat_info in _CATEGORY_MAP.items():
            cat_items = [item_results[n] for n in cat_info["items"] if n in item_results]
            if cat_items:
                category_results.append(cls.score_category(cat_name, cat_items))

        return cls.score_total(category_results, flags=flags)
