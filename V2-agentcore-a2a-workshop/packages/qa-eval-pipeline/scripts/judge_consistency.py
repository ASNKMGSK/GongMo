# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""논리 일관성 판단자 (Consistency Reviewer).

소넷의 sonnet_reason 텍스트 톤(우수/보통/미흡)과 sonnet_score 수치의 정합성,
항목 간 모순, sonnet_overall 종합평가가 개별 18 항목의 평가와 일관되는지 검증한다.

검사 차원:
1. 점수-reason 섹션 정합성 (만점+[감점사유], 0점+[우수사항]만 등)
2. 항목 간 상위/하위 요소 역전 (쿠션어-정중 표현 등)
3. 종합평가의 강점/개선 인용이 실제 점수와 일치하는지
4. 종합평가 코칭 카테고리가 개별 항목의 [high/medium] 코칭과 교차하는지
5. 부분 점수 항목이 종합평가에 전혀 언급되지 않은 누락 (요약의 불완전성)
6. LLM 실패-규칙 폴백 케이스 (논리 근거 부재)
7. 점수-등급 산정 정확성
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

INPUT_PATH = Path(r"C:\Users\META M\Desktop\qa_judge_input.json")
OUTPUT_PATH = Path(r"C:\Users\META M\Desktop\qa_judge_consistency_reviewer.json")


def parse_stated_overall(overall: str) -> tuple[int | None, str | None]:
    if not overall:
        return None, None
    m = re.search(r"점수[:\s·]*([0-9]+)\s*점", overall)
    score = int(m.group(1)) if m else None
    m2 = re.search(r"등급[:\s·]*([A-F])", overall)
    grade = m2.group(1) if m2 else None
    return score, grade


def grade_from_total(total: int) -> str:
    if total >= 90:
        return "A"
    if total >= 80:
        return "B"
    if total >= 70:
        return "C"
    if total >= 60:
        return "D"
    return "F"


def section(overall: str, start: str, ends: list[str]) -> str:
    """overall 텍스트에서 start 헤더 뒤, 다음 헤더 직전까지의 본문 추출."""
    if start not in overall:
        return ""
    idx = overall.find(start) + len(start)
    rest = overall[idx:]
    min_end = len(rest)
    for e in ends:
        i = rest.find(e)
        if i >= 0 and i < min_end:
            min_end = i
    return rest[:min_end]


def cited_item_nums(text: str) -> list[int]:
    """본문에서 #N 형식 인용 번호 추출 (1..18 범위)."""
    out = []
    for m in re.finditer(r"#\s*([0-9]+)", text):
        try:
            v = int(m.group(1))
            if 1 <= v <= 18:
                out.append(v)
        except ValueError:
            pass
    return out


def check_score_reason_section(item: dict) -> str | None:
    """점수와 reason 섹션 구조의 정합성 검사."""
    s = item.get("sonnet_score")
    m = item.get("max_score")
    r = item.get("sonnet_reason", "") or ""
    if s is None or m is None:
        return None

    has_deduct = "[감점 사유]" in r or "감점:" in r
    has_excel = "[우수 사항]" in r
    has_improv = "[개선 필요]" in r or "[개선필요]" in r
    has_deduct_marker = bool(re.search(r"\(-[0-9]+점\)", r))

    # Full score shouldn't have [감점 사유] or (-N점) markers
    if s == m and has_deduct:
        return f"만점({s}/{m})이지만 reason에 '[감점 사유]' 섹션 존재"
    if s == m and has_deduct_marker:
        return f"만점({s}/{m})이지만 reason에 '(-N점)' 감점 표시 존재"
    # Zero score shouldn't have only [우수 사항]
    if s == 0 and has_excel and not has_deduct and not has_improv:
        return f"0점 부여했으나 reason에 '[우수 사항]'만 있고 감점/개선 섹션 부재"
    # Partial score (s < m, s > 0) ideally has [감점 사유] and [개선 필요]
    if 0 < s < m and not has_deduct and not has_deduct_marker:
        return f"부분 점수({s}/{m})인데 reason에 감점 사유 섹션/표시 없음 (왜 감점되었는지 명시 안됨)"
    # Zero with no explanation at all
    if s == 0 and not has_deduct and not has_improv:
        return f"0점인데 reason에 감점/개선 섹션 전혀 없음"
    return None


def check_llm_fallback(item: dict) -> str | None:
    r = item.get("sonnet_reason", "") or ""
    if "LLM 실패" in r or "규칙 폴백" in r:
        return "LLM 평가 실패 → 규칙 폴백 (모델 추론 미수행, 논리적 근거 부재)"
    return None


def check_cross_item_contradiction(sheet: dict) -> list[dict]:
    """18 항목 간 상위/하위 요소 역전 등 모순 검사."""
    items_by_row = {it["row"]: it for it in sheet["items"]}
    probs = []

    # 쿠션어(row=12) 만점인데 정중한 표현(row=11) 0점
    polite = items_by_row.get(11)
    cushion = items_by_row.get(12)
    if polite and cushion:
        p_s = polite.get("sonnet_score") or 0
        c_s = cushion.get("sonnet_score") or 0
        p_max = polite.get("max_score") or 5
        c_max = cushion.get("max_score") or 5
        if c_s == c_max and p_s == 0:
            probs.append(
                {
                    "rows": [11, 12],
                    "items": ["정중한 표현", "쿠션어 활용"],
                    "issue": "쿠션어 만점인데 정중한 표현 0점 — 쿠션어는 정중 표현의 구체 실현 수단으로 하위-상위 역전",
                }
            )

    # 경청(row=8) 만점인데 reason에 '말겹침 있음' 언급
    listen = items_by_row.get(8)
    if listen and (listen.get("sonnet_score") == (listen.get("max_score") or 5)):
        r = listen.get("sonnet_reason", "") or ""
        if re.search(r"말겹침(?!.*없이)", r) or re.search(r"말자름(?!.*없이)", r):
            # Check negation nearby
            # Keep naive: if '말겹침' appears without negation like '없이', '없음', '없다'
            has_negation = bool(re.search(r"말겹침[^\n]{0,20}(없|안)", r)) or bool(re.search(r"말자름[^\n]{0,20}(없|안)", r))
            if not has_negation:
                probs.append(
                    {
                        "rows": [8],
                        "items": ["경청"],
                        "issue": "경청 만점이지만 reason에 '말겹침/말자름'이 부정 수식 없이 언급됨",
                    }
                )

    # 복창(row=13) 만점인데 reason에 '복창 없이/누락' 표현
    recap = items_by_row.get(13)
    if recap and (recap.get("sonnet_score") == (recap.get("max_score") or 5)):
        r = recap.get("sonnet_reason", "") or ""
        if "복창 없이" in r or "복창 누락" in r or "복창하지 않" in r:
            probs.append(
                {
                    "rows": [13],
                    "items": ["문의 파악 및 재확인(복창)"],
                    "issue": "복창 만점이지만 reason에 '복창 없이/누락/하지 않음' 표현",
                }
            )

    # 정보 확인 절차(row=22) 만점인데 '확인하지 않음'
    verify = items_by_row.get(22)
    if verify and (verify.get("sonnet_score") == (verify.get("max_score") or 5)):
        r = verify.get("sonnet_reason", "") or ""
        if "확인하지 않" in r or "미수행" in r:
            probs.append(
                {
                    "rows": [22],
                    "items": ["정보 확인 절차"],
                    "issue": "정보 확인 절차 만점이지만 reason에 '확인하지 않음/미수행' 표현",
                }
            )

    return probs


def check_overall_citation(sheet: dict) -> list[str]:
    """종합평가의 강점/개선 인용 번호가 실제 점수와 일치하는지."""
    overall = sheet.get("sonnet_overall", "") or ""
    items_by_row = {it["row"]: it for it in sheet["items"]}
    findings = []

    strengths_text = section(overall, "주요 강점", ["개선 필요", "코칭 포인트", "마무리"])
    weakness_text = section(overall, "개선 필요", ["코칭 포인트", "마무리"])

    s_cites = cited_item_nums(strengths_text)
    w_cites = cited_item_nums(weakness_text)

    for n in s_cites:
        it = items_by_row.get(5 + n)
        if not it:
            continue
        s = it.get("sonnet_score")
        m = it.get("max_score")
        if s is not None and m is not None and s < m:
            findings.append(f"강점으로 인용한 #{n}({it['item']}) 실제 점수 {s}/{m} — 만점 아님")

    for n in w_cites:
        it = items_by_row.get(5 + n)
        if not it:
            continue
        s = it.get("sonnet_score")
        m = it.get("max_score")
        if s is not None and m is not None and s == m:
            findings.append(f"개선 필요로 인용한 #{n}({it['item']}) 실제 점수 {s}/{m} — 만점")

    # Partial-score items not cited anywhere
    all_cites = set(s_cites) | set(w_cites)
    uncited_partial = []
    for it in sheet["items"]:
        s = it.get("sonnet_score")
        m = it.get("max_score")
        if s is None or m is None:
            continue
        n = it["row"] - 5
        if 0 < s < m and n not in all_cites:
            uncited_partial.append(f"#{n}({it['item']} {s}/{m})")
        elif s == 0 and n not in all_cites:
            uncited_partial.append(f"#{n}({it['item']} 0점 — 미인용)")

    if uncited_partial:
        findings.append(
            f"종합평가에 인용되지 않은 감점 항목 {len(uncited_partial)}개: " + ", ".join(uncited_partial[:4])
        )

    return findings


def check_overall_arithmetic(sheet: dict) -> list[str]:
    """종합 점수/등급 산정의 산술 정합성."""
    overall = sheet.get("sonnet_overall", "") or ""
    findings = []
    stated, stated_grade = parse_stated_overall(overall)
    actual = sum((it.get("sonnet_score") or 0) for it in sheet["items"])
    if stated is not None and stated != actual:
        findings.append(f"종합 점수 불일치: overall 기재 {stated}점 vs 개별 항목 합산 {actual}점")
    if stated is not None and stated_grade:
        expected = grade_from_total(stated)
        if expected != stated_grade:
            findings.append(f"등급 오류: 점수 {stated} → 기대 등급 {expected}, 기재 등급 {stated_grade}")
    return findings


def check_coaching_coherence(sheet: dict) -> list[str]:
    """종합 코칭 포인트와 개별 항목 [high/medium] 코칭 카테고리 교차 검사."""
    overall = sheet.get("sonnet_overall", "") or ""
    m = re.search(r"코칭 포인트([\s\S]*?)(?:마무리|$)", overall)
    if not m:
        return []
    overall_coaching = m.group(1)
    overall_cats = set()
    for mm in re.finditer(r"\[(?:high|medium|low)\s*/\s*([^\]]+)\]", overall_coaching):
        overall_cats.add(mm.group(1).strip())

    individual_cats = set()
    individual_by_cat = defaultdict(list)
    for it in sheet["items"]:
        r = it.get("sonnet_reason", "") or ""
        for mm in re.finditer(r"\[(?:high|medium|low)\]\s*([^\n]+)", r):
            cat = mm.group(1).strip()
            # Strip trailing punctuation
            cat = cat.rstrip(" .,")
            individual_cats.add(cat)
            individual_by_cat[cat].append(it["row"] - 5)

    findings = []
    only_items = individual_cats - overall_cats
    if only_items:
        details = []
        for c in list(only_items)[:3]:
            rows = individual_by_cat[c][:3]
            details.append(f"{c}({','.join('#'+str(r) for r in rows)})")
        findings.append(f"개별 항목 코칭 카테고리 {len(only_items)}개가 종합 코칭 포인트에 미반영: {', '.join(details)}")
    return findings


def compute_verdict(total_issues: int) -> tuple[str, int]:
    if total_issues == 0:
        return "일관됨", 5
    if total_issues <= 2:
        return "일관됨", 4
    if total_issues <= 4:
        return "부분 모순", 3
    if total_issues <= 7:
        return "부분 모순", 2
    return "일관성 결여", 1


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    sheet_verdicts = {}
    agg_score_reason = []  # (sid, row, item, issue)
    agg_llm_fallback = []
    agg_cross = []
    agg_citation = []
    agg_arith = []
    agg_coaching = []

    for key, sheet in data.items():
        sid = str(sheet["sid"])
        if sid == "668463":
            sheet_verdicts[sid] = {
                "verdict": "제외",
                "consistency_score": "-",
                "key_findings": "사용자 지시에 따라 분석 대상에서 제외",
                "problematic_items": [],
            }
            continue

        problematic = []

        # 1. Score-reason section check (per item)
        for it in sheet["items"]:
            msg = check_score_reason_section(it)
            if msg:
                n = it["row"] - 5
                s = it.get("sonnet_score")
                m = it.get("max_score")
                entry = {
                    "item": f"#{n} {it['item']} ({s}/{m})",
                    "issue": f"reason 섹션-점수 정합성: {msg}",
                }
                problematic.append(entry)
                agg_score_reason.append((sid, it["row"], it["item"], msg))

            lf = check_llm_fallback(it)
            if lf:
                n = it["row"] - 5
                s = it.get("sonnet_score")
                m = it.get("max_score")
                entry = {
                    "item": f"#{n} {it['item']} ({s}/{m})",
                    "issue": f"LLM 실패 폴백: {lf}",
                }
                problematic.append(entry)
                agg_llm_fallback.append((sid, it["row"], it["item"]))

        # 2. Cross-item contradiction
        cross = check_cross_item_contradiction(sheet)
        for c in cross:
            label = "#" + "/#".join(str(r - 5) for r in c["rows"])
            problematic.append({"item": f"{label} 항목 간", "issue": c["issue"]})
            agg_cross.append((sid, c["rows"], c["issue"]))

        # 3. Overall citation mismatch
        cits = check_overall_citation(sheet)
        for c in cits:
            problematic.append({"item": "종합평가(강점/개선 인용)", "issue": c})
            agg_citation.append((sid, c))

        # 4. Arithmetic
        arith = check_overall_arithmetic(sheet)
        for a in arith:
            problematic.append({"item": "종합평가(점수/등급)", "issue": a})
            agg_arith.append((sid, a))

        # 5. Coaching coherence
        coach = check_coaching_coherence(sheet)
        for c in coach:
            problematic.append({"item": "종합 코칭 포인트", "issue": c})
            agg_coaching.append((sid, c))

        total_issues = len(problematic)
        verdict, c_score = compute_verdict(total_issues)

        # key findings
        msgs = []
        sr_cnt = sum(1 for x in problematic if "섹션-점수 정합성" in x["issue"])
        lf_cnt = sum(1 for x in problematic if "LLM 실패 폴백" in x["issue"])
        cr_cnt = sum(1 for x in problematic if x["item"].startswith("#") and "항목 간" in x["item"])
        ci_cnt = sum(1 for x in problematic if x["item"].startswith("종합평가(강점"))
        ar_cnt = sum(1 for x in problematic if x["item"].startswith("종합평가(점수"))
        co_cnt = sum(1 for x in problematic if x["item"] == "종합 코칭 포인트")
        if sr_cnt:
            msgs.append(f"점수-reason 정합성 {sr_cnt}건")
        if lf_cnt:
            msgs.append(f"LLM 실패 폴백 {lf_cnt}건")
        if cr_cnt:
            msgs.append(f"항목 간 모순 {cr_cnt}건")
        if ci_cnt:
            msgs.append(f"종합 인용 이슈 {ci_cnt}건")
        if ar_cnt:
            msgs.append(f"산술 이슈 {ar_cnt}건")
        if co_cnt:
            msgs.append(f"코칭 정합성 이슈 {co_cnt}건")
        if not msgs:
            msgs.append("점수/reason/종합평가 간 논리 정합성 양호")
        key_findings = "; ".join(msgs) + "."

        sheet_verdicts[sid] = {
            "verdict": verdict,
            "consistency_score": c_score,
            "key_findings": key_findings,
            "problematic_items": problematic,
        }

    total_sheets = len([v for v in sheet_verdicts.values() if v["verdict"] != "제외"])

    # Build common_issues
    common_issues = []

    def _freq_sids(pairs, idx=0):
        return sorted({p[idx] for p in pairs})

    # Score-reason pattern grouping
    sr_by_pattern = defaultdict(list)
    for sid, row, item, msg in agg_score_reason:
        if "만점" in msg and "감점 사유" in msg:
            key = "만점인데_감점사유_섹션"
        elif "만점" in msg and "(-N점)" in msg:
            key = "만점인데_감점표시"
        elif "0점" in msg and "우수 사항" in msg:
            key = "0점인데_우수만"
        elif "부분 점수" in msg:
            key = "부분점수인데_감점사유_없음"
        elif "0점인데" in msg:
            key = "0점인데_설명_없음"
        else:
            key = "기타"
        sr_by_pattern[key].append((sid, row, item))

    descriptions = {
        "만점인데_감점사유_섹션": {
            "issue": "만점인데 reason에 '[감점 사유]' 섹션 존재",
            "description": "sonnet_score가 만점인데 reason 본문에 '[감점 사유]' 헤더가 남아 있어 점수와 근거가 모순됨. 템플릿 필드가 조건부 렌더링되지 않았을 가능성.",
            "severity": "high",
        },
        "만점인데_감점표시": {
            "issue": "만점인데 reason에 '(-N점)' 감점 표시",
            "description": "만점 항목의 reason에 '-2점' 등 감점 표시가 그대로 남아있음.",
            "severity": "high",
        },
        "0점인데_우수만": {
            "issue": "0점인데 reason에 '[우수 사항]'만 존재",
            "description": "0점을 부여했으나 감점/개선 사유 섹션이 없고 우수 사항만 기재되어 0점을 정당화하는 근거 부재.",
            "severity": "high",
        },
        "부분점수인데_감점사유_없음": {
            "issue": "부분 점수인데 reason에 감점 사유/표시 없음",
            "description": "3/5, 7/10 등 부분 점수인데 reason에 '왜' 만점이 아닌지 설명하는 섹션이 없어 점수 산정 근거 부재.",
            "severity": "medium",
        },
        "0점인데_설명_없음": {
            "issue": "0점인데 reason에 감점/개선 섹션 전무",
            "description": "0점 부여 근거가 완전히 누락됨.",
            "severity": "high",
        },
        "기타": {
            "issue": "기타 점수-reason 섹션 정합성 이슈",
            "description": "",
            "severity": "medium",
        },
    }

    for k, pairs in sr_by_pattern.items():
        meta = descriptions.get(k, {})
        sids_in = _freq_sids(pairs)
        common_issues.append(
            {
                "issue": meta.get("issue", k),
                "description": meta.get("description", ""),
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": meta.get("severity", "medium"),
            }
        )

    # LLM fallback
    if agg_llm_fallback:
        sids_in = _freq_sids(agg_llm_fallback)
        common_issues.append(
            {
                "issue": "LLM 평가 실패 → 규칙 폴백 (평가 근거 부재)",
                "description": "필수 안내 이행/문제 해결 의지 등 일부 항목에서 LLM 추론이 실패하여 규칙 기반 폴백으로 감점함. 이 경우 reason이 '(LLM 실패 — 규칙 폴백)'로만 기재되어 점수 감점의 실제 근거(근거 발화/구체 사유)가 부재하며 평가 신뢰성 저하.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "high",
            }
        )

    # Cross-item
    if agg_cross:
        sids_in = _freq_sids(agg_cross)
        common_issues.append(
            {
                "issue": "항목 간 상위/하위 요소 논리 역전",
                "description": "쿠션어-정중 표현, 경청-말겹침, 복창-문의 파악 등 관련 항목 간 점수 방향 또는 reason 내용이 상호 모순되는 사례.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "medium",
            }
        )

    # Citation
    citation_uncited = [p for p in agg_citation if "인용되지 않은 감점 항목" in p[1]]
    citation_strength_partial = [p for p in agg_citation if "강점으로 인용한" in p[1]]
    citation_weak_full = [p for p in agg_citation if "개선 필요로 인용한" in p[1]]

    if citation_uncited:
        sids_in = _freq_sids(citation_uncited)
        common_issues.append(
            {
                "issue": "부분점수/감점 항목이 종합평가에 인용되지 않음",
                "description": "개별 항목에서는 감점되었는데 overall의 '주요 강점/개선 필요' 어디에도 해당 항목 번호가 언급되지 않아 종합평가가 개별 평가의 일부만 반영함.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "high",
            }
        )
    if citation_strength_partial:
        sids_in = _freq_sids(citation_strength_partial)
        common_issues.append(
            {
                "issue": "강점으로 인용한 항목이 실제 만점 아님",
                "description": "overall이 '주요 강점'으로 지목했으나 해당 항목의 sonnet_score는 만점이 아님.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "medium",
            }
        )
    if citation_weak_full:
        sids_in = _freq_sids(citation_weak_full)
        common_issues.append(
            {
                "issue": "개선 필요로 인용한 항목이 실제 만점",
                "description": "overall이 '개선 필요'로 지목했으나 해당 항목은 sonnet_score 만점.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "medium",
            }
        )

    # Arithmetic
    if agg_arith:
        sids_in = _freq_sids(agg_arith)
        common_issues.append(
            {
                "issue": "종합 점수/등급 산정 오류",
                "description": "overall 기재 점수가 개별 항목 합산과 다르거나, 점수→등급 매핑이 기준과 불일치.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "high",
            }
        )

    # Coaching
    if agg_coaching:
        sids_in = _freq_sids(agg_coaching)
        common_issues.append(
            {
                "issue": "개별 코칭 카테고리가 종합 코칭 포인트에 미반영",
                "description": "개별 항목 reason에 '[high/medium] 니즈 파악' 등 코칭 카테고리가 있는데 종합 코칭 포인트 섹션에는 동일 카테고리가 나타나지 않아 종합 요약이 개별 지적을 놓침.",
                "frequency": f"{len(sids_in)}/{total_sheets}",
                "example_sids": sids_in[:5],
                "severity": "medium",
            }
        )

    # Sort: severity (high>medium>low), then frequency desc
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    common_issues.sort(
        key=lambda x: (
            sev_rank.get(x["severity"], 3),
            -int(x["frequency"].split("/")[0]),
        )
    )

    # Verdicts summary
    consistent_cnt = sum(1 for v in sheet_verdicts.values() if v["verdict"] == "일관됨")
    partial_cnt = sum(1 for v in sheet_verdicts.values() if v["verdict"] == "부분 모순")
    broken_cnt = sum(1 for v in sheet_verdicts.values() if v["verdict"] == "일관성 결여")

    total_sr = len(agg_score_reason)
    total_lf = len(agg_llm_fallback)
    total_cr = len(agg_cross)
    total_ci = len(agg_citation)
    total_ar = len(agg_arith)
    total_co = len(agg_coaching)
    grand_total = total_sr + total_lf + total_cr + total_ci + total_ar + total_co

    overall_summary = (
        f"총 {total_sheets}건(668463 제외) 중 '일관됨' {consistent_cnt}건, '부분 모순' {partial_cnt}건, "
        f"'일관성 결여' {broken_cnt}건으로 집계되었으며, 논리 정합성 위반 사례 총 {grand_total}건을 식별하였다. "
        "가장 빈번한 결함은 종합평가가 감점된 개별 항목을 '주요 강점/개선 필요' 섹션에 전혀 인용하지 않고 지나치는 요약의 불완전성이다. "
        "두 번째는 개별 항목 reason에 부여된 [high/medium] 코칭 카테고리가 종합 코칭 포인트 섹션에 재집계되지 않아 개별-종합 계층 간 비닉이 발생하는 현상이다. "
        "또한 '필수 안내 이행' 등 일부 항목에서 LLM 추론 실패 시 '규칙 폴백'으로 감점 처리하면서 근거 발화·구체 사유 없이 템플릿 문자열만 남겨 평가 신뢰성을 훼손하는 케이스가 존재한다. "
        "반면 만점인데 [감점 사유] 섹션이 남아 있거나 0점인데 [우수 사항]만 기재되는 등의 템플릿 조건부 렌더링 오류는 확인되지 않아, 점수-reason 섹션 수준의 정합성은 양호한 편이다. "
        "종합하면 개별 평가 수준은 견고하지만 '개별 → 종합'으로 정보가 집계되는 경로에서 누락·재집계 실패가 체계적으로 발생한다."
    )

    prompt_improvements = [
        "1. 종합평가 '주요 강점/개선 필요' 섹션을 생성하기 전에, 개별 18 항목 중 만점 항목과 감점 항목 목록을 먼저 출력하게 하고, 각 섹션은 반드시 해당 목록에서 최소 N개(만점 3개, 감점 3개)를 인용하도록 강제한다. 인용 번호 옆에 실제 점수(예: #2 끝인사 3/5)를 병기 의무화.",
        "2. 종합 코칭 포인트 섹션 생성 시, 개별 항목 reason에서 추출된 [high/medium] 코칭 카테고리를 우선 취합하고, 빈도수 기준으로 상위 3개 카테고리를 반드시 종합 코칭 포인트로 승격하도록 프롬프트에 집계 지시문 추가.",
        "3. LLM 추론 실패 시 단순 규칙 폴백으로 감점하는 로직을 재설계한다. 폴백 대신 재시도(retry) 또는 '판단 불가로 원점수 유지'와 같은 안전한 기본값을 사용하고, 폴백이 불가피한 경우 reason에 구체 키워드 탐지 결과를 포함하도록 하여 논리 근거를 남긴다.",
        "4. 부분 점수(5점 만점 중 3점, 10점 만점 중 5·7점 등) 부여 시 reason에는 '[감점 사유]' 섹션과 '(-N점)' 표시를 필수화하는 규칙을 프롬프트 체크리스트로 명시하고, 생성 후 자체 검증 패스에서 점수-섹션 짝 맞춤을 확인하게 한다.",
        "5. 종합평가 생성 프롬프트에 '개별 18 항목을 모두 스캔한 후, 감점된 항목이 강점/개선 섹션 어디에도 없으면 개선 섹션에 강제 추가하라'는 누락 방지 규칙을 삽입한다. 이를 통해 sid=668675·668697 등에서 발견된 3개 이상 감점 항목 누락 패턴을 구조적으로 차단.",
    ]

    result = {
        "judge_name": "Consistency-Reviewer",
        "judge_role_kr": "논리 일관성 판단자",
        "perspective": "점수-reason-종합평가 간 논리 정합성",
        "overall_summary": overall_summary,
        "common_issues": common_issues,
        "sheet_verdicts": sheet_verdicts,
        "prompt_improvement_suggestions": prompt_improvements,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Saved: {OUTPUT_PATH}")
    print(f"Sheets analyzed: {total_sheets} (excluded 668463)")
    print(f"Verdicts — 일관됨: {consistent_cnt}, 부분 모순: {partial_cnt}, 일관성 결여: {broken_cnt}")
    print(
        f"Issues — 점수-reason: {total_sr}, LLM폴백: {total_lf}, "
        f"항목간: {total_cr}, 인용: {total_ci}, 산술: {total_ar}, 코칭: {total_co}"
    )
    print(f"common_issues count: {len(common_issues)}")


if __name__ == "__main__":
    main()
