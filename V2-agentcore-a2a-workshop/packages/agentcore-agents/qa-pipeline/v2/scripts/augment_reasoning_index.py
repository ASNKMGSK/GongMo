# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
kolon reasoning_index 보강 — 각 item 에 5~7개 새 레코드 추가.

기존 r_001~r_005 외에 r_006~r_012 추가하여 stdev 신뢰도 향상.

실행: python v2/scripts/augment_reasoning_index.py
"""

from __future__ import annotations
import json
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "tenants" / "kolon" / "reasoning_index"

# 항목별 추가 레코드 (item_number → list of dict)
ADDITIONS: dict[int, list[dict]] = {
    1: [  # 첫인사
        {"score": 5, "rationale": "인사말 + 소속 + 상담사명 3요소 + 적극적 응대 멘트까지 포함된 모범 첫인사", "quote_example": "상담사: 안녕하세요 고객님 코오롱 고객센터 박OO 상담사입니다 무엇을 도와드릴까요", "evaluator_id": "senior_a", "tags": ["full_compliance", "polite_opening", "active_engagement"]},
        {"score": 5, "rationale": "정중한 인사 + 회사명 + 본인 성함 명확히 전달, 짧지만 완결성 있음", "quote_example": "상담사: 반갑습니다 코오롱몰 김OO입니다 어떤 도움이 필요하신가요", "evaluator_id": "senior_c", "tags": ["full_compliance", "concise"]},
        {"score": 3, "rationale": "회사명 + 상담사명 있지만 인사말 누락, 1요소 미충족", "quote_example": "상담사: 코오롱 고객센터 이OO입니다 말씀하세요", "evaluator_id": "senior_b", "tags": ["missing_greeting"]},
        {"score": 3, "rationale": "인사말 + 회사명 있으나 '상담사' 라는 호칭만 있고 본인 성명 미공지 1요소 미충족", "quote_example": "상담사: 안녕하세요 코오롱 고객센터 상담사입니다", "evaluator_id": "senior_a", "tags": ["missing_name", "anonymous"]},
        {"score": 0, "rationale": "인사 없이 바로 본론 진입, 첫인사 절차 완전 누락", "quote_example": "상담사: 무슨 일이세요", "evaluator_id": "senior_c", "tags": ["no_opening", "abrupt"]},
        {"score": 0, "rationale": "응답이 어조도 거칠고 인사 절차 전무, 무성의한 응대", "quote_example": "상담사: 네", "evaluator_id": "senior_b", "tags": ["no_opening", "rude"]},
        {"score": 5, "rationale": "전화 연결 직후 차분한 톤으로 인사 + 소속 + 본인 성명 + 용건 질의 모두 충족", "quote_example": "상담사: 네 안녕하세요 코오롱몰 고객센터 정OO 상담사입니다 무엇을 도와드릴까요", "evaluator_id": "senior_b", "tags": ["full_compliance", "calm_tone"]},
    ],
    2: [  # 끝인사
        {"score": 5, "rationale": "마무리 인사 + 추가 문의 확인 + 본인 성명 재언급으로 완결성 높은 종료 응대", "quote_example": "상담사: 더 궁금하신 점 있으신가요 없으시면 코오롱 고객센터 김OO이었습니다 좋은 하루 되세요", "evaluator_id": "senior_a", "tags": ["full_closing", "additional_inquiry_check"]},
        {"score": 5, "rationale": "감사 인사 + 추가 문의 확인 + 종료 인사 정중히 마무리", "quote_example": "상담사: 문의해 주셔서 감사합니다 더 도와드릴 일이 있으실까요 없으시면 좋은 하루 보내세요", "evaluator_id": "senior_c", "tags": ["polite_closing", "additional_inquiry_check"]},
        {"score": 3, "rationale": "종료 인사는 있으나 추가 문의 확인 누락 1요소 미충족", "quote_example": "상담사: 네 감사합니다 좋은 하루 되세요", "evaluator_id": "senior_b", "tags": ["missing_additional_inquiry"]},
        {"score": 3, "rationale": "추가 문의 확인은 했으나 마무리 인사 형식적 1요소 미충족", "quote_example": "상담사: 더 궁금하신 점 있으세요 네 그럼 끊겠습니다", "evaluator_id": "senior_a", "tags": ["abrupt_closing"]},
        {"score": 0, "rationale": "마무리 인사·추가 문의 확인·감사 표현 전부 누락, 일방적 종료", "quote_example": "상담사: 네 끊습니다", "evaluator_id": "senior_c", "tags": ["no_closing", "abrupt"]},
        {"score": 0, "rationale": "고객 발화 중 일방 종료, 종료 멘트 전무", "quote_example": "상담사: (전화 종료)", "evaluator_id": "senior_b", "tags": ["no_closing", "premature_end"]},
    ],
    3: [  # 말겹침 — 프로토타입 제외, 만점 고정
        {"score": 5, "rationale": "프로토타입 제외 항목 — 항상 만점 처리", "quote_example": "(평가 제외)", "evaluator_id": "senior_a", "tags": ["skipped_default_full"]},
        {"score": 5, "rationale": "STT 신뢰도 한계로 평가 제외, 기본 만점", "quote_example": "(평가 제외)", "evaluator_id": "senior_b", "tags": ["skipped_default_full"]},
    ],
    4: [  # 호응 및 공감
        {"score": 5, "rationale": "고객의 불편 호소에 명확한 사과 + 공감 표현 + 재발 방지 약속까지 풍부한 응대", "quote_example": "상담사: 정말 죄송합니다 고객님 많이 불편하셨겠어요 다시 한 번 점검해서 같은 일이 없도록 하겠습니다", "evaluator_id": "senior_a", "tags": ["empathy_strong", "apology"]},
        {"score": 5, "rationale": "고객 감정 그대로 인정 + 사과 + 즉시 조치 약속 3요소 충족", "quote_example": "상담사: 아 정말 속상하셨겠어요 죄송합니다 바로 확인해서 처리해 드리겠습니다", "evaluator_id": "senior_c", "tags": ["empathy_strong", "immediate_action"]},
        {"score": 3, "rationale": "사과는 있으나 공감 표현이 형식적, 감정 인정 부족", "quote_example": "상담사: 네 죄송합니다 확인해 보겠습니다", "evaluator_id": "senior_b", "tags": ["empathy_weak", "formal_apology"]},
        {"score": 3, "rationale": "공감 표현 사용했으나 짧고 한정적, 추가 안내 미연결", "quote_example": "상담사: 아 그러시군요 네", "evaluator_id": "senior_a", "tags": ["empathy_minimal"]},
        {"score": 0, "rationale": "고객 불편 호소에 무반응으로 일관, 공감/사과 전무", "quote_example": "상담사: 네 그래서 어떻게 해드릴까요", "evaluator_id": "senior_c", "tags": ["no_empathy", "transactional"]},
        {"score": 0, "rationale": "고객 감정 무시 + 책임 전가 멘트, 부정적 응대", "quote_example": "상담사: 그건 고객님 사정이고요 저희가 어떻게 할 수 없습니다", "evaluator_id": "senior_b", "tags": ["no_empathy", "blame_shift"]},
    ],
    5: [  # 대기 멘트
        {"score": 5, "rationale": "대기 사전 안내 + 사유 설명 + 양해 구함 3요소 완벽 충족", "quote_example": "상담사: 조회하는 동안 잠시만 기다려 주시겠습니까 약 30초 정도 소요됩니다", "evaluator_id": "senior_a", "tags": ["full_hold_notice", "duration_specified"]},
        {"score": 5, "rationale": "대기 양해 구함 + 대기 후 감사 인사까지 완결성 높음", "quote_example": "상담사: 잠시만 기다려 주시겠습니까 ... 기다려 주셔서 감사합니다 고객님", "evaluator_id": "senior_c", "tags": ["full_hold_notice", "post_hold_thanks"]},
        {"score": 3, "rationale": "대기 안내는 있으나 사유 설명 누락, 형식적 응대", "quote_example": "상담사: 잠시만요", "evaluator_id": "senior_b", "tags": ["minimal_hold_notice"]},
        {"score": 3, "rationale": "대기 후 감사 인사는 있으나 대기 사전 안내 짧음", "quote_example": "상담사: 잠시만요 ... 기다려 주셔서 감사합니다", "evaluator_id": "senior_a", "tags": ["weak_pre_hold"]},
        {"score": 0, "rationale": "사전 안내 없이 일방적 대기 시작, 양해 구함 전무", "quote_example": "상담사: (5초간 무음)", "evaluator_id": "senior_c", "tags": ["no_hold_notice", "silent_hold"]},
        {"score": 0, "rationale": "대기 후 복귀 시 인사 없이 바로 본론, 무성의", "quote_example": "상담사: (대기 복귀) 결과는 이렇습니다", "evaluator_id": "senior_b", "tags": ["no_post_hold_thanks"]},
    ],
    6: [  # 정중한 표현
        {"score": 5, "rationale": "전 발화에 걸쳐 존댓말 + 호칭 사용 + 어조 부드러움 유지", "quote_example": "상담사: 네 고객님 그러시면 이렇게 도와드리면 어떨까요", "evaluator_id": "senior_a", "tags": ["polite_throughout", "respectful"]},
        {"score": 5, "rationale": "정중한 호칭 + 부드러운 어미 + 명확한 의사전달 모두 충족", "quote_example": "상담사: 고객님 말씀하신 부분은 다음과 같이 안내해 드리겠습니다", "evaluator_id": "senior_c", "tags": ["polite_throughout", "clear_phrasing"]},
        {"score": 3, "rationale": "기본 존댓말 사용하나 일부 발화에서 사물존칭 사용", "quote_example": "상담사: 이 상품은 사이즈가 다양하세요", "evaluator_id": "senior_b", "tags": ["honorific_to_object"]},
        {"score": 3, "rationale": "전반적으로 존댓말이지만 1회 반말 종결어미 사용", "quote_example": "상담사: 그래야지 그래도 배송 가능성이 높아져요", "evaluator_id": "senior_a", "tags": ["informal_ending_once"]},
        {"score": 0, "rationale": "반말 사용 + 명령조 응대 다수 발화에서 확인", "quote_example": "상담사: 그건 거기 가서 물어봐", "evaluator_id": "senior_c", "tags": ["informal_speech", "imperative"]},
        {"score": 0, "rationale": "비속어 + 사적 감정 노출 + 무례한 어조", "quote_example": "상담사: 아 진짜 짜증나네 자꾸 같은 말 하시면 어떡해요", "evaluator_id": "senior_b", "tags": ["rude", "frustration_leak"]},
    ],
    7: [  # 쿠션어
        {"score": 5, "rationale": "거절·불가 안내 시 양해 구함 + 사과 + 대안 제시 3요소 모두 충족", "quote_example": "상담사: 죄송합니다만 해당 상품은 현재 재고가 없으셔서 다른 색상으로 안내드려도 괜찮으실까요", "evaluator_id": "senior_a", "tags": ["cushion_full", "alternative_proposed"]},
        {"score": 5, "rationale": "민감한 정보 요청 전 양해 멘트 정중히 사용", "quote_example": "상담사: 본인 확인을 위해 죄송하지만 성함과 휴대폰 번호 말씀 부탁드릴게요", "evaluator_id": "senior_c", "tags": ["cushion_full", "permission_seeking"]},
        {"score": 3, "rationale": "쿠션어 일부 사용 (죄송)했으나 양해 구함 부족", "quote_example": "상담사: 죄송하지만 해당 상품 안 됩니다", "evaluator_id": "senior_b", "tags": ["cushion_partial"]},
        {"score": 3, "rationale": "쿠션어 1회 사용했으나 이후 거절·불가 안내 시 무미건조", "quote_example": "상담사: 죄송합니다 ... 그건 안 돼요 ... 그것도 안 돼요", "evaluator_id": "senior_a", "tags": ["cushion_inconsistent"]},
        {"score": 0, "rationale": "거절·불가 안내 시 쿠션어 전무, 단답형 차단", "quote_example": "상담사: 안 됩니다", "evaluator_id": "senior_c", "tags": ["no_cushion", "blunt_refusal"]},
        {"score": 0, "rationale": "양해 구함 없이 일방 통보, 고객 양해 사항 무시", "quote_example": "상담사: 그냥 그렇게 처리됩니다", "evaluator_id": "senior_b", "tags": ["no_cushion", "unilateral"]},
    ],
    8: [  # 니즈 파악
        {"score": 5, "rationale": "고객 문의 핵심 정확히 복창 + 의문형 재확인까지 완결", "quote_example": "상담사: 사이즈 교환 요청 문의가 맞으시죠 어떤 사이즈로 교환 원하시나요", "evaluator_id": "senior_a", "tags": ["recap_full", "question_for_clarification"]},
        {"score": 5, "rationale": "고객 발화 요약 + 추가 정보 요청 적절", "quote_example": "상담사: 그러니까 핸드백 버클 부분이 망가졌다는 말씀이시죠 언제부터 그런 증상이 나타나셨을까요", "evaluator_id": "senior_c", "tags": ["recap_full", "follow_up_question"]},
        {"score": 3, "rationale": "복창은 있으나 단순 확인에 그침, 추가 정보 미요청", "quote_example": "상담사: 네 알겠습니다", "evaluator_id": "senior_b", "tags": ["weak_recap"]},
        {"score": 3, "rationale": "고객 의도 일부만 파악, 누락 부분 있음", "quote_example": "상담사: 반품 접수 도와드릴게요", "evaluator_id": "senior_a", "tags": ["partial_understanding"]},
        {"score": 0, "rationale": "고객 의도 파악 시도 없이 바로 본인 안내로 진행", "quote_example": "상담사: 네 그러면 이렇게 해드리겠습니다", "evaluator_id": "senior_c", "tags": ["no_recap", "assumption"]},
        {"score": 0, "rationale": "고객이 두 번 반복 후에도 의도 파악 못함, 동문서답", "quote_example": "상담사: 그래서 뭐를 원하신다고요 다시 한 번 말씀해 주세요", "evaluator_id": "senior_b", "tags": ["misunderstanding"]},
    ],
    9: [  # 고객정보 확인
        {"score": 5, "rationale": "본인확인 사유 설명 + 정보 요청 + 확인 완료 안내 3요소 충족", "quote_example": "상담사: 본인 확인을 위해 성함과 휴대폰 번호 말씀 부탁드립니다 ... 네 확인되셨습니다", "evaluator_id": "senior_a", "tags": ["full_verification", "explicit_completion"]},
        {"score": 5, "rationale": "단계별 본인확인 절차 (이름→번호→주문번호) 체계적 진행", "quote_example": "상담사: 성함 부탁드립니다 ... 휴대폰 번호 부탁드립니다 ... 주문번호 알려주시겠어요", "evaluator_id": "senior_c", "tags": ["systematic_verification"]},
        {"score": 3, "rationale": "본인확인 진행했으나 사유 설명 누락", "quote_example": "상담사: 성함이랑 번호 말씀해 주세요", "evaluator_id": "senior_b", "tags": ["no_verification_reason"]},
        {"score": 3, "rationale": "본인확인은 했으나 확인 완료 안내 누락", "quote_example": "상담사: 성함과 번호 부탁드릴게요 ... (바로 본론)", "evaluator_id": "senior_a", "tags": ["no_completion_notice"]},
        {"score": 0, "rationale": "본인확인 절차 생략 후 민감 정보 처리 진행", "quote_example": "상담사: 네 그러면 환불 처리해 드릴게요", "evaluator_id": "senior_c", "tags": ["skipped_verification"]},
    ],
    10: [  # 설명 명확성 (max 10)
        {"score": 10, "rationale": "정책 + 절차 + 일정 + 비용 모든 요소 구조화하여 설명, 수치 명시", "quote_example": "상담사: 회수 기사 영업일 2-3일 내 방문, 입고 후 검수 영업일 2-3일, 자사 상품은 1회 무상이고 외부 입점은 5천원 부담입니다", "evaluator_id": "senior_a", "tags": ["full_clarity", "structured", "policy_cited"]},
        {"score": 10, "rationale": "조건별 분기 명확 + 수치 + 출처 인용으로 완벽한 설명", "quote_example": "상담사: 자사 브랜드 코오롱스포츠 골든베어는 1회 무상 교환이고 그 외 외부 입점 브랜드는 왕복 택배비 5천원이 발생합니다 검수 결과 불량 판정 시에는 비용 환급됩니다", "evaluator_id": "senior_c", "tags": ["full_clarity", "branched_conditions"]},
        {"score": 7, "rationale": "핵심 정보는 정확하나 일부 표현이 완화 ('정도', '~같아요')", "quote_example": "상담사: 회수는 한 2-3일 정도 걸릴 거 같애요", "evaluator_id": "senior_b", "tags": ["mid_clarity", "mitigated_expression"]},
        {"score": 5, "rationale": "장황한 설명 중 핵심 일부 전달되나 조건 불명확", "quote_example": "상담사: 그게 상품 불량이면 무상이고 아니면 비용이 뭐 그럴 수도 있고", "evaluator_id": "senior_a", "tags": ["verbose", "ambiguous_conditions"]},
        {"score": 5, "rationale": "용어 통일 부족 + 핵심과 부수 정보 혼재", "quote_example": "상담사: 그러니까 회수 기사가 와서 가져가고 그 다음에 뭐 검수하고 그러면 환불도 되고 교환도 되고 그래요", "evaluator_id": "senior_c", "tags": ["mid_clarity", "unstructured"]},
        {"score": 0, "rationale": "설명 거의 없음 + 업무 회피", "quote_example": "상담사: 그건 저도 잘 모르겠는데요", "evaluator_id": "senior_b", "tags": ["work_avoidance", "no_explanation"]},
    ],
    11: [  # 두괄식
        {"score": 5, "rationale": "결론 즉답 + 부연 설명 순서 명확", "quote_example": "상담사: 교환 가능하십니다 다만 1회 무상이시고 회수 기사가 방문드릴 거예요", "evaluator_id": "senior_a", "tags": ["top_down", "conclusion_first"]},
        {"score": 5, "rationale": "고객 질문에 결론 한 문장 즉답 후 조건 부연", "quote_example": "상담사: 네 가능하십니다 단, 택과 라벨 제거되지 않은 상태여야 합니다", "evaluator_id": "senior_c", "tags": ["top_down", "concise_conclusion"]},
        {"score": 3, "rationale": "조건/배경 먼저 나오고 결론이 뒤에 배치", "quote_example": "상담사: 상품 불량인지 검수해봐야 하는데 일단 접수는 가능하세요", "evaluator_id": "senior_b", "tags": ["bottom_up", "lead_delayed"]},
        {"score": 3, "rationale": "결론이 답변 중간에 섞여 있어 즉각성 부족", "quote_example": "상담사: 음 그게 지금 말씀하신 내용이 그러니까 교환은 가능은 하세요", "evaluator_id": "senior_a", "tags": ["mid_lead", "hesitant"]},
        {"score": 0, "rationale": "결론 없이 조건만 나열, 고객 질문에 답 부재", "quote_example": "상담사: 이게 뭐 경우에 따라서 다르고요 상황 봐야 알 수 있어요", "evaluator_id": "senior_c", "tags": ["no_conclusion", "evasive"]},
    ],
    12: [  # 적극성 (문제해결)
        {"score": 5, "rationale": "고객 미요청 사항까지 선제적 안내 + 대안 다수 제시", "quote_example": "상담사: 추가로 무료 회수 서비스도 같이 안내드릴까요 그리고 다음 주문 시 사용 가능한 5천원 쿠폰도 발급해 드리겠습니다", "evaluator_id": "senior_a", "tags": ["proactive", "extra_value"]},
        {"score": 5, "rationale": "고객 요구 충족 + 추가 솔루션 + 후속 채널 안내", "quote_example": "상담사: 이번엔 이렇게 처리해 드리고요 다음에 비슷한 일 있으시면 카카오톡 채널로도 빠르게 도움 받으실 수 있어요", "evaluator_id": "senior_c", "tags": ["proactive", "channel_guidance"]},
        {"score": 3, "rationale": "기본 응대만 수행, 추가 안내 없음", "quote_example": "상담사: 네 처리해 드렸습니다", "evaluator_id": "senior_b", "tags": ["basic_only"]},
        {"score": 3, "rationale": "고객 요청만 처리하고 추가 정보 제공 미흡", "quote_example": "상담사: 반품 접수 완료되었습니다", "evaluator_id": "senior_a", "tags": ["minimal_solution"]},
        {"score": 0, "rationale": "고객 요청 회피 + 타 부서로 떠넘김", "quote_example": "상담사: 그건 저희 부서가 아니라 다른 데로 다시 전화해 보세요", "evaluator_id": "senior_c", "tags": ["work_avoidance", "transfer_excuse"]},
    ],
    13: [  # 부연 설명
        {"score": 5, "rationale": "주요 안내 후 관련 정책·예외사항·후속 절차까지 부연", "quote_example": "상담사: 환불 영업일 3-5일 내 입금되시고요 카드 결제는 다음 청구일 차감 가능합니다 포인트는 1-2일 내 자동 복원돼요", "evaluator_id": "senior_a", "tags": ["full_supplement", "edge_cases_covered"]},
        {"score": 5, "rationale": "안내 외 추가 유의사항·연락처 안내까지 친절히", "quote_example": "상담사: 회수 기사 도착 전 SMS 받으시고요 만약 부재 시 재방문 일정은 1:1 채팅으로 조정 가능합니다", "evaluator_id": "senior_c", "tags": ["full_supplement", "contact_info"]},
        {"score": 3, "rationale": "기본 안내 후 부연 설명 단편적", "quote_example": "상담사: 환불 며칠 걸려요", "evaluator_id": "senior_b", "tags": ["minimal_supplement"]},
        {"score": 0, "rationale": "안내 후 후속 정보 전혀 제공 안 함", "quote_example": "상담사: 끝났어요", "evaluator_id": "senior_a", "tags": ["no_supplement"]},
    ],
    14: [  # 사후 안내
        {"score": 5, "rationale": "처리 완료 후 후속 일정 + 확인 채널 + 추가 문의 경로 안내", "quote_example": "상담사: 회수 완료되면 SMS 안내드리고 환불 입금 시에도 알림 갑니다 추가 문의는 마이페이지 또는 1:1 채팅으로 부탁드릴게요", "evaluator_id": "senior_a", "tags": ["full_followup", "multi_channel"]},
        {"score": 5, "rationale": "완료 후 대기 시간 + 다음 액션 + 비상 연락처 명확", "quote_example": "상담사: 영업일 3일 내 환불 입금되시고 만약 5일 이상 지연 시 고객센터로 다시 연락 부탁드립니다", "evaluator_id": "senior_c", "tags": ["full_followup", "contingency_provided"]},
        {"score": 3, "rationale": "사후 일정만 안내, 확인 방법 미설명", "quote_example": "상담사: 며칠 안에 처리됩니다", "evaluator_id": "senior_b", "tags": ["weak_followup"]},
        {"score": 0, "rationale": "처리 완료 알림 없이 일방 종료", "quote_example": "상담사: 네 끝났습니다 좋은 하루 되세요", "evaluator_id": "senior_a", "tags": ["no_followup"]},
    ],
    15: [  # 정확한 안내 (max 10)
        {"score": 10, "rationale": "RAG 정책 정보와 100% 일치한 안내, 수치·조건 모두 정확", "quote_example": "상담사: 자사 브랜드 1회 무상 교환이고 외부 입점 브랜드는 왕복 5천원입니다 검수 후 불량 판정 시 비용 환급됩니다", "evaluator_id": "senior_a", "tags": ["full_accuracy", "policy_aligned"]},
        {"score": 10, "rationale": "정책 출처 인용 + 정확한 수치 + 조건별 분기 안내", "quote_example": "상담사: 회수 영업일 2-3일, 검수 2-3일 영업일 안에 환불 처리 됩니다 이는 코오롱몰 표준 절차입니다", "evaluator_id": "senior_c", "tags": ["full_accuracy", "source_cited"]},
        {"score": 5, "rationale": "주요 정보 정확하나 일부 세부사항 누락", "quote_example": "상담사: 한 5천원 정도 발생하실 수 있어요", "evaluator_id": "senior_b", "tags": ["partial_accuracy"]},
        {"score": 0, "rationale": "정책과 다른 안내 + 정정 시도 없음 (오안내)", "quote_example": "상담사: 무조건 무상이에요 비용 발생 안 합니다", "evaluator_id": "senior_a", "tags": ["misinformation", "no_correction"]},
        {"score": 0, "rationale": "수치 오류 + 고객 이의제기 무시", "quote_example": "상담사: 영업일 7일 걸려요 (실제 2-3일)", "evaluator_id": "senior_c", "tags": ["wrong_numbers", "ignored_correction"]},
    ],
    16: [  # 필수 안내
        {"score": 5, "rationale": "intent (반품) 에 매핑된 필수 스크립트 5요소 모두 이행", "quote_example": "상담사: 택 미제거 + 회수 일정 + 반송장 보관 + 검수 절차 + 환불 일정 모두 안내", "evaluator_id": "senior_a", "tags": ["all_mandatory_done"]},
        {"score": 5, "rationale": "필수 사항 + 선택 사항까지 친절하게 안내", "quote_example": "상담사: 모든 필수 안내 + 추가 보상 옵션 안내", "evaluator_id": "senior_c", "tags": ["all_mandatory_done", "extra_info"]},
        {"score": 3, "rationale": "필수 5요소 중 1요소 누락 (반송장 보관 안내)", "quote_example": "상담사: 회수 일정 + 검수 + 환불은 안내했으나 반송장 보관 누락", "evaluator_id": "senior_b", "tags": ["mandatory_partial"]},
        {"score": 3, "rationale": "필수 항목 1-2개 누락 (택 확인 / 반송장)", "quote_example": "상담사: 회수 + 환불만 안내, 택 확인·반송장 보관 누락", "evaluator_id": "senior_a", "tags": ["mandatory_partial"]},
        {"score": 0, "rationale": "필수 안내 3개 이상 누락, 핵심 절차 미이행", "quote_example": "상담사: 네 알아서 보내주세요 (전 항목 누락)", "evaluator_id": "senior_c", "tags": ["mandatory_missing"]},
    ],
    17: [  # PII 본인확인 절차
        {"score": 5, "rationale": "본인확인 멘트 + 양해 표현 + PII 토큰 + 확인 완료 안내 4단계 모두 이행", "quote_example": "상담사: 본인 확인을 위해 죄송합니다만 성함과 휴대폰 번호 부탁드립니다 ... 네 확인되셨습니다", "evaluator_id": "senior_a", "tags": ["full_pii_procedure"]},
        {"score": 5, "rationale": "단계별 절차 명확 + 정보 보호 사유 명시", "quote_example": "상담사: 소중한 정보 보호를 위해 본인 확인 진행하겠습니다 성함과 연락처 부탁드릴게요", "evaluator_id": "senior_c", "tags": ["full_pii_procedure", "rationale_explained"]},
        {"score": 3, "rationale": "본인확인 진행했으나 양해 표현 누락", "quote_example": "상담사: 성함과 번호 알려주세요", "evaluator_id": "senior_b", "tags": ["no_apology", "blunt_request"]},
        {"score": 3, "rationale": "확인 완료 멘트 누락, 절차 종료 모호", "quote_example": "상담사: 성함과 번호 부탁드립니다 ... (확인 완료 안내 없이 본론 진행)", "evaluator_id": "senior_a", "tags": ["no_completion_notice"]},
        {"score": 0, "rationale": "본인확인 절차 생략 후 PII 처리 진행", "quote_example": "상담사: (본인확인 없이) 환불 진행해 드릴게요", "evaluator_id": "senior_c", "tags": ["skipped_pii_procedure"]},
    ],
    18: [  # PII 보호 준수
        {"score": 5, "rationale": "PII 발화 시 마스킹 처리 + 제3자 노출 금지 + 추가 동의 확인 모두 충족", "quote_example": "상담사: 등록된 010-XXXX-XXXX 번호로 SMS 발송됩니다", "evaluator_id": "senior_a", "tags": ["full_compliance", "masked_pii"]},
        {"score": 5, "rationale": "고객 정보 재확인 시 부분 노출만 사용, 정책 준수", "quote_example": "상담사: 김OO 고객님 맞으시죠 등록 주소지 첫 글자만 확인 부탁드립니다", "evaluator_id": "senior_c", "tags": ["full_compliance", "partial_disclosure"]},
        {"score": 3, "rationale": "전체 정보 노출 1회 (휴대폰 번호 풀로 발화)", "quote_example": "상담사: 010-1234-5678 맞으시죠", "evaluator_id": "senior_b", "tags": ["pii_full_disclosure_minor"]},
        {"score": 0, "rationale": "주민번호 / 카드번호 풀로 발화, 정보보호법 위반 위험", "quote_example": "상담사: 800101-1234567 맞으시죠", "evaluator_id": "senior_a", "tags": ["pii_critical_leak"]},
        {"score": 0, "rationale": "제3자에게 고객 주소·연락처 무단 공개", "quote_example": "상담사: 고객님 주소가 서울시 ... 이고 번호는 ... 입니다 (배송기사에게 전화)", "evaluator_id": "senior_c", "tags": ["pii_third_party_leak"]},
    ],
}


def main() -> int:
    if not DIR.is_dir():
        print(f"[error] {DIR} 없음")
        return 1
    total_added = 0
    for item_no, new_recs in ADDITIONS.items():
        # 파일명 패턴: NN_*.json
        files = sorted(DIR.glob(f"{item_no:02d}_*.json"))
        if not files:
            print(f"[skip] item {item_no}: 파일 없음")
            continue
        path = files[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[error] {path.name} parse 실패: {e}")
            continue
        existing_ids = {r.get("record_id") for r in data.get("reasoning_records", [])}
        next_idx = max(int(r.get("record_id", "r_000").split("_")[-1]) for r in data["reasoning_records"]) + 1 if data.get("reasoning_records") else 1

        added = 0
        for rec in new_recs:
            rid = f"r_{next_idx:03d}"
            next_idx += 1
            if rid in existing_ids:
                continue
            full_rec = {
                "record_id": rid,
                "score": rec["score"],
                "rationale": rec["rationale"],
                "quote_example": rec["quote_example"],
                "evaluator_id": rec["evaluator_id"],
                "tags": rec.get("tags", []),
                "stub_seed": True,
            }
            data["reasoning_records"].append(full_rec)
            added += 1
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] item {item_no:2d}: +{added} (total now {len(data['reasoning_records'])})")
        total_added += added
    print(f"\n[done] 총 추가: {total_added} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
