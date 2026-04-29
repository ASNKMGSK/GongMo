# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Consistency-Reviewer 시트별 토론 라운드 응답.

다른 9명 판단자 verdict를 검토하고, 논리 일관성 관점에서
동의/반박/입장차를 명시한 100-200자 토론 발화를 생성한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INPUT_PATH = Path(r"C:\Users\META M\Desktop\qa_judge_debate_input.json")
MY_RESULT_PATH = Path(r"C:\Users\META M\Desktop\qa_judge_consistency_reviewer.json")
OUTPUT_PATH = Path(r"C:\Users\META M\Desktop\qa_judge_debate_consistency_reviewer.json")

MY_NAME = "Consistency-Reviewer"


# 시트별 수동 작성: 다른 9명의 입장과 본인 (논리 일관성) 관점 교차
# 형식: (stance, statement) — statement 100-200자
DEBATE = {
    "668437": {
        "stance": "부분 동의",
        "statement": (
            "Score-Calibrator·Meta-Judge·Bias-Detector 가 지적한 '필수 안내 이행 0점(LLM 실패 폴백)'은 저도 동일 지적. "
            "다만 Critical-Judge의 '심각한 결함'은 과해, 점수-reason 섹션 정합성과 산술은 정상이라 전반적 구조는 일관. "
            "Domain-Expert의 '도메인 무지' 지적은 논리 외부 이슈라 본 관점에서는 유보."
        ),
    },
    "668451": {
        "stance": "동의",
        "statement": (
            "총점 92 동점이나 Bias-Detector·Meta-Judge·Score-Calibrator 가 지적한 '상쇄 편향'에 일부 동의. "
            "다만 저의 섹션·산술·인용 정합성 검사에서는 위반 0건이라 논리 일관성 자체는 양호. "
            "Coaching·Customer-Empathy 의 '우수' 평가는 개별 항목 구조 건강성과 맞물려 Sonnet-Advocate 중립 입장 지지."
        ),
    },
    "668463": {
        "stance": "제외",
        "statement": "제외 - 통화 중단",
    },
    "668464": {
        "stance": "부분 동의",
        "statement": (
            "Score-Calibrator(거의 일치)·Evidence-Auditor(근거 적절)·Sonnet-Advocate(옹호 가능) 입장에 수렴. "
            "저의 유일한 지적은 '#15 정확한 안내 5/10' 감점이 overall 강점/개선 어디에도 인용되지 않은 요약 누락 1건. "
            "Domain-Expert의 '소유권 구조 파악' 평가는 도메인 특수 이슈라 논리 층위에서는 보조적."
        ),
    },
    "668481": {
        "stance": "동의",
        "statement": (
            "Score-Calibrator·Meta-Judge·Critical-Judge 의 'LLM 폴백 남발' 지적에 전면 동의. "
            "본인 검토에서도 LLM 실패 폴백 2건(문제 해결 의지·부연 설명) + 감점 4건 종합 미인용이 겹쳐 전체에서 유일하게 '부분 모순' 판정. "
            "Bias-Detector 의 '편향 영향 큼'과 같은 진단."
        ),
    },
    "668488": {
        "stance": "동의",
        "statement": (
            "Score-Calibrator·Evidence-Auditor·Sonnet-Advocate·Coaching-Value 가 공통으로 '양호' 판정. "
            "저의 점수-reason·산술·인용·코칭 정합성 6차원 모두 위반 0건으로 판정 consistency 점수 5점 부여. "
            "Critical-Judge가 특이하게 엄한 입장이지만 본 관점 기준 반박."
        ),
    },
    "668507": {
        "stance": "부분 동의",
        "statement": (
            "Sonnet-Advocate·Coaching-Value-Judge 의 '옹호 가능/우수' 쪽에 가까움. "
            "다만 '#16 필수 안내 이행 3/5' 감점이 overall 어디에도 인용되지 않은 요약 누락 1건 확인. "
            "Bias-Detector·Critical-Judge 의 편향·결함 주장은 단일 미세 이슈 수준이라 과중 해석이라 판단."
        ),
    },
    "668526": {
        "stance": "동의",
        "statement": (
            "Evidence-Auditor·Consistency·Sonnet-Advocate·Coaching-Value 공통 '양호'. "
            "본인 6차원 정합성 검사 위반 0건으로 최고점 5 부여. "
            "Critical-Judge의 엄격 평가와 Customer-Empathy의 미스매치 지적이 있으나, 논리 구조 자체는 결함 없음."
        ),
    },
    "668542": {
        "stance": "부분 동의",
        "statement": (
            "본 검토에서 감점 4건이 종합평가에 전혀 인용되지 않고 코칭 카테고리 2개가 비닉된 요약 품질 이슈 확인. "
            "Score-Calibrator·Meta-Judge 의 '불일치 영향' 지적과 맞닿음. "
            "하지만 섹션 구조와 산술은 모두 정상이라 Critical-Judge 의 '심각' 판정은 과대."
        ),
    },
    "668605": {
        "stance": "부분 동의",
        "statement": (
            "Meta-Judge·Score-Calibrator 가 지목한 '#12 문제 해결 의지 LLM 폴백'에 저도 동일 지적. "
            "추가로 #12 감점이 overall 인용 누락으로 요약 정합성까지 훼손. "
            "다만 다른 17개 항목 섹션·산술 모두 정상이라 Critical-Judge 의 '결함' 규모와 본인 규모 인식은 온건한 편."
        ),
    },
    "668610": {
        "stance": "동의",
        "statement": (
            "Evidence-Auditor·Sonnet-Advocate·Coaching-Value 공통 '적절/옹호/우수' 입장 지지. "
            "본인 6차원 위반 0건. "
            "Score-Calibrator 가 지적한 점수 차이는 있을 수 있으나 논리 정합성 차원에서는 모두 규칙 준수하여 최고점 5 부여."
        ),
    },
    "668675": {
        "stance": "부분 반박",
        "statement": (
            "Domain-Expert·Customer-Empathy 의 '부적합/미스매치' 평가가 있으나 본 검토는 논리 정합성 관점으로 구분. "
            "핵심 지적은 감점 7개(#5,#8,#9,#10,#12,#13,#16)가 overall 요약에 미인용된 최대 누락 사례 + 니즈 파악 코칭 2회 비닉. "
            "Score·Bias 가 지적한 차이보다 '요약 불완전성'이 더 심각."
        ),
    },
    "668697": {
        "stance": "부분 반박",
        "statement": (
            "다수가 '양호/옹호 가능' 쪽이나 본 검토에서 감점 7개(#5,#8,#9,#10,#13,#14,#15) 전부 overall 미인용 + 코칭 3개 비닉. "
            "668675와 쌍둥이 패턴으로 '요약 누락'이 가장 심각한 사례 중 하나. "
            "Meta-Judge 의 '7개 모호 항목 집중' 지적과 공명."
        ),
    },
    "668736": {
        "stance": "부분 동의",
        "statement": (
            "대다수 '양호/옹호 가능'에 수렴. "
            "본인 검토에서는 '경청 및 소통' 코칭 카테고리가 개별(#5)에 있지만 종합 코칭에 승격되지 않은 비닉 1건. "
            "Critical-Judge 의 '엄격 평가' 방향에 부분 동의하되, 인용·산술·섹션 모두 정상이라 규모는 제한적."
        ),
    },
    "668771": {
        "stance": "동의",
        "statement": (
            "Evidence-Auditor·Consistency·Sonnet-Advocate·Coaching-Value 공통 '적절/양호/우수'. "
            "본인 6차원 위반 0건, 최고점 5 부여. "
            "Critical-Judge 가 여전히 엄격 입장이나 논리 정합성 차원에서 반박하며 현 평가 견고성 유지 입장."
        ),
    },
    "668797": {
        "stance": "동의",
        "statement": (
            "Score-Calibrator·Evidence·Sonnet-Advocate·Coaching-Value 공통 '양호/적절/우수'. "
            "본인 6차원 위반 0건, 최고점 5. "
            "Critical-Judge 의 이례적 엄격 평가와 별도로 본 관점에서 결함 미발견."
        ),
    },
    "668847": {
        "stance": "동의",
        "statement": (
            "대다수 판단자 긍정 쪽. "
            "본인 6차원 정합성 위반 0건 최고점 5 부여. "
            "Customer-Empathy 의 미스매치나 Domain-Expert 의 도메인 이슈는 별도 층위라 논리 구조 일관성과 분리해 해석."
        ),
    },
    "668853": {
        "stance": "동의",
        "statement": (
            "Sonnet-Advocate·Coaching-Value 의 '옹호/우수', Evidence-Auditor '적절'에 수렴. "
            "본인 6차원 위반 0건. "
            "재고 부족 맥락에서 '부족'이라는 단어가 reason 에 등장하나 긍정 평가 근거로 맥락 사용되어 점수-톤 모순 아님."
        ),
    },
    "668865": {
        "stance": "부분 동의",
        "statement": (
            "본인 검토에서 감점 2건(#12,#13) overall 미인용 + 코칭 3개(적극성·업무정확도·인사예절) 비닉 확인. "
            "Score-Calibrator·Meta-Judge 의 '차이 집중' 지적과 공명. "
            "다만 Critical-Judge 의 '결함' 강도보다는 '요약 불완전성' 수준이 더 정확한 진단."
        ),
    },
    "668899": {
        "stance": "동의",
        "statement": (
            "전반 긍정 판단에 수렴. "
            "본인 6차원 위반 0건 최고점 5. "
            "Critical-Judge·Customer-Empathy 가 일부 비판적이나 점수-reason-종합평가 구조는 정상 작동."
        ),
    },
    "668916": {
        "stance": "부분 동의",
        "statement": (
            "Sonnet-Advocate·Coaching-Value '옹호 가능/우수' 입장에 가까움. "
            "본인 검토는 '#13 부연 설명 3/5' 단일 감점의 overall 미인용 1건만 지적. "
            "Critical-Judge 의 엄격 입장보다는 경미한 요약 누락 수준."
        ),
    },
    "668927": {
        "stance": "부분 동의",
        "statement": (
            "본인 검토의 핵심 지적은 코칭 카테고리 3개(니즈 파악·인사 예절·종합) 비닉. "
            "인용·산술·섹션은 모두 정상. "
            "Score-Calibrator·Meta-Judge 의 '설계 영향' 지적은 종합→개별 집계 경로 불완전성과 맞물림."
        ),
    },
    "668941": {
        "stance": "부분 동의",
        "statement": (
            "대다수 긍정 쪽. "
            "본인 검토는 '#13 부연 설명 3/5' 단일 감점 overall 미인용 1건. "
            "전체 구조는 정상 작동하나 '감점 최소 인용 의무' 규칙 부재로 개별 감점이 종합에서 지워지는 패턴 재현."
        ),
    },
    "668963": {
        "stance": "동의",
        "statement": (
            "Evidence-Auditor·Sonnet-Advocate·Coaching-Value 공통 '적절/옹호/우수'. "
            "본인 6차원 위반 0건 최고점 5. "
            "Critical-Judge·Customer-Empathy 의 비판적 입장에 논리 구조 차원에서 반박."
        ),
    },
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # Sanity: ensure 24 sheet ids present in debate
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        din = json.load(f)
    input_sids = set(din["sheets"].keys())
    our_sids = set(DEBATE.keys())
    missing = input_sids - our_sids
    extra = our_sids - input_sids
    if missing:
        raise RuntimeError(f"Missing sids in DEBATE: {missing}")
    if extra:
        raise RuntimeError(f"Extra sids in DEBATE: {extra}")

    # Length check (exclude 제외 sheet)
    out_lengths = []
    for sid, v in DEBATE.items():
        if sid == "668463":
            continue
        l = len(v["statement"])
        out_lengths.append((sid, l))
        if not (100 <= l <= 220):
            print(f"WARN {sid} statement length {l} (target 100-200)")

    # Build output preserving sid order from input
    responses = {}
    for sid in din["sheets"].keys():
        responses[sid] = DEBATE[sid]

    result = {
        "judge_name": MY_NAME,
        "debate_responses": responses,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Saved: {OUTPUT_PATH}")
    print(f"Sheet count: {len(responses)}")
    print("Statement lengths (non-excluded):")
    for sid, l in out_lengths:
        mark = "OK" if 100 <= l <= 220 else "OUT"
        print(f"  {sid}: {l}자 [{mark}]")


if __name__ == "__main__":
    main()
