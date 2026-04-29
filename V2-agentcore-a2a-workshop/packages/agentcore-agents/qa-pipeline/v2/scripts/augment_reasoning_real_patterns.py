# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
kolon reasoning_index 2차 보강 — 실제 qa_samples transcript 패턴 기반.

기준 transcript 예시들 (qa_samples/668xxx_kolon_*.json) 에서 자주 등장하는 패턴:
- 교환/반품 혼동 (택배기사 오회수, 송장 발행 재처리)
- 본인확인 절차 (성함 + 휴대폰 번호 + 본인 맞으십니까)
- 양해/대기 (죄송합니다만 잠시만 기다려 주십시오)
- 반품비 정확 안내 (자사 1회 무상, 외부 입점 5천원, 단순 변심 2,500원)
- 마일리지/포인트 정확 안내 (이벤트 시 한정 지급)
- 추가 문의 확인 + 상담사명 재언급으로 끝인사

실행: python v2/scripts/augment_reasoning_real_patterns.py
"""

from __future__ import annotations
import json
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "tenants" / "kolon" / "reasoning_index"

# qa_samples 의 실제 transcript 에서 추출된 표현 기반 records
ADDITIONS: dict[int, list[dict]] = {
    1: [
        {"score": 5, "rationale": "공식 회사명 + 성함 + 용건 질의 3요소 충족, 응답 후 '예' 응대까지 자연스러움", "quote_example": "상담사: 반갑습니다 코오롱 고객센터 박OO입니다 무엇을 도와드릴까요 예", "evaluator_id": "senior_a", "tags": ["full_compliance", "real_pattern"]},
        {"score": 5, "rationale": "전화 연결 직후 첫 발화에서 인사 + 회사명 + 본인 성명 + 용건 질의 즉시 진행", "quote_example": "상담사: 안녕하세요 코오롱몰 고객센터 김OO 상담사입니다 어떤 도움이 필요하실까요", "evaluator_id": "senior_b", "tags": ["full_compliance", "natural_flow"]},
    ],
    2: [
        {"score": 5, "rationale": "추가 문의 확인 + 상담사명 재언급 + 종료 인사 정중", "quote_example": "상담사: 다른 혹시 문의사항 있으실까요 ... 예 상담원 박OO이었습니다 좋은 하루 보내세요", "evaluator_id": "senior_a", "tags": ["full_closing", "name_repeat", "real_pattern"]},
        {"score": 5, "rationale": "추가 문의 1차 확인 후 후속 안내까지 한 번 더 전달, 상담사명 마지막 재공지", "quote_example": "상담사: 네 다른 문의사항은 없으실까요 ... 회수 잘 되는지 끝까지 확인하고 변동사항 있으면 연락드리겠습니다 상담원 OO이었습니다", "evaluator_id": "senior_c", "tags": ["full_closing", "followup_promise"]},
        {"score": 3, "rationale": "추가 문의 확인은 했지만 종료 인사가 형식적, 상담사명 재언급 없음", "quote_example": "상담사: 다른 거 더 있으세요 없으시면 끊겠습니다", "evaluator_id": "senior_b", "tags": ["abrupt_closing", "no_name_repeat"]},
    ],
    5: [
        {"score": 5, "rationale": "양해 표현 + 사과 + 대기 안내 3요소, 대기 후 사과 한 번 더", "quote_example": "상담사: 죄송합니다만 잠시만 기다려 주십시오 ... 잠시만 기다려주시오 죄송합니다 잠시만 기다려 주십시오", "evaluator_id": "senior_a", "tags": ["full_hold_notice", "real_pattern", "double_apology"]},
        {"score": 5, "rationale": "확인 사유 + 양해 + 시간 안내까지 명확", "quote_example": "상담사: 잠시만 기다려 주시면 송장 추가 발행 처리 도와드리겠습니다 약 30초 소요됩니다", "evaluator_id": "senior_c", "tags": ["full_hold_notice", "duration_specified"]},
    ],
    7: [
        {"score": 5, "rationale": "민감 요청 (회수 변경) 전 양해 + 사과 + 대안 제시 3요소 완벽", "quote_example": "상담사: 죄송합니다만 잠시만 기다려 주십시오 확인해 보고 말씀 드리겠습니다", "evaluator_id": "senior_a", "tags": ["cushion_full", "real_pattern"]},
        {"score": 5, "rationale": "정보 요청 시 사유 명시 + 양해", "quote_example": "상담사: 본인 확인을 위해 죄송합니다만 성함과 휴대폰 번호 말씀 부탁드립니다", "evaluator_id": "senior_b", "tags": ["cushion_full", "purpose_explained"]},
        {"score": 3, "rationale": "쿠션어 사용했으나 단답형 거절 후 대안 제시 미흡", "quote_example": "상담사: 죄송합니다만 그건 안 돼요", "evaluator_id": "senior_c", "tags": ["cushion_minimal"]},
    ],
    8: [
        {"score": 5, "rationale": "고객 발화 정확히 정리 + 의문형 재확인 (본인확인 + 상황 파악 동시)", "quote_example": "상담사: 그러면 지금 현재 반품 접수되어 있는 상품은 가지고 계신 거세요 교환 상품만 회수가 되신 거구요", "evaluator_id": "senior_a", "tags": ["recap_full", "clarification_question", "real_pattern"]},
        {"score": 5, "rationale": "복잡한 상황 (교환/반품 혼동) 을 정확히 분리 정리 + 후속 액션 제안", "quote_example": "상담사: 이 회수된 상품은 저희가 그냥 다시 반송을 해드릴까요 아니면 지금이라도 반품으로 접수를 해드릴까요", "evaluator_id": "senior_b", "tags": ["recap_full", "options_proposed"]},
    ],
    9: [
        {"score": 5, "rationale": "성함 + 휴대폰 번호 요청 → 본인 맞는지 의문형 재확인 → 확인 완료 응답 4단계 모두 충족", "quote_example": "상담사: 먼저 연락 주신 고객님의 성함과 휴대폰 번호를 말씀해 주시겠습니까 ... 예 OOO 고객님 본인 맞으십니까 ... 예 소중한 정보 확인 감사합니다", "evaluator_id": "senior_a", "tags": ["full_verification", "real_pattern", "explicit_completion"]},
        {"score": 5, "rationale": "본인확인 의문형 ('본인 맞으십니까') + 응답 후 감사 표현", "quote_example": "상담사: 예 OO 고객님 본인 맞으십니까 ... 예 소중한 정보 확인 감사합니다 고객님", "evaluator_id": "senior_c", "tags": ["full_verification", "thanks_after_verification"]},
    ],
    10: [
        {"score": 10, "rationale": "회수 일정 + 검수 일정 + 환불 일정 + 결제수단별 분기까지 구조화 명확", "quote_example": "상담사: 내일부터 기사가 이 삼 일 내에 방문드릴 예정이고 반품하는 상품은 저희 쪽에 도착이 되면 검수는 이 삼 일 결제하신 수단으로 환불은 영업일 기준 삼 일에서 오 일 정도 소요가 되세요", "evaluator_id": "senior_a", "tags": ["full_clarity", "structured", "real_pattern"]},
        {"score": 10, "rationale": "고객 의문 사항 (반품 후 송장 보관) 까지 미리 안내, 실수 사과 + 정정 명확", "quote_example": "상담사: 환불이 완료되는 시점까지 택배기사가 전달해드린 그 운송장 지금 가지고 계신 거죠 그 운송장을 보관해 주시면 됩니다 교환 상품도 교환 완료 시점까지 송장을 하나 더 드릴 거예요 그것도 교환 완료되는 시점까지 보관을 해 주시면 됩니다", "evaluator_id": "senior_c", "tags": ["full_clarity", "cross_referencing"]},
        {"score": 7, "rationale": "핵심 정보 정확하나 '한 번에 정리' 가 아닌 분절적 안내", "quote_example": "상담사: 내일 기사가 가요 그러고 검수하면 환불 됩니다 며칠 걸려요", "evaluator_id": "senior_b", "tags": ["mid_clarity", "fragmented"]},
        {"score": 5, "rationale": "장황 + 핵심 늦게 등장, 고객 재질문 다수 발생", "quote_example": "상담사: 그러니까 이게 일단 회수가 되면 그 다음에 검수가 들어가는데요 검수가 끝나야 환불이 되거든요 근데 그게 또 결제 수단에 따라서 다르고", "evaluator_id": "senior_a", "tags": ["verbose", "delayed_core"]},
    ],
    13: [
        {"score": 5, "rationale": "회수 주소 변경 가능 여부 + 운송장 보관 + 검수 절차 + 환불 일정 + 알림 채널 모두 부연", "quote_example": "상담사: 회수할 곳 주소 변동 없으신 거죠 ... 도착이 되면 검수는 이 삼 일 영업일 기준 삼 일에서 오 일 정도 소요가 되세요 환불 입금되시면 알림 갑니다", "evaluator_id": "senior_a", "tags": ["full_supplement", "real_pattern"]},
        {"score": 5, "rationale": "본 안내 + 변동 시 추가 연락 약속까지 부연", "quote_example": "상담사: 회수가 잘 되는지만 끝까지 확인해 보구요 변동사항 있으면 제가 연락을 드리겠습니다", "evaluator_id": "senior_c", "tags": ["full_supplement", "proactive_followup"]},
    ],
    14: [
        {"score": 5, "rationale": "처리 완료 후 후속 알림 채널 + 변동 시 연락 약속 + 마무리 인사 일관", "quote_example": "상담사: 환불이 완료되는 시점까지 운송장 보관해 주시면 됩니다 변동사항 있으면 제가 연락을 드리겠습니다", "evaluator_id": "senior_a", "tags": ["full_followup", "real_pattern"]},
        {"score": 5, "rationale": "교환 상품 후속 처리 일정 + 양해 요청까지 친절", "quote_example": "상담사: 교환 상품은 저희 쪽에 도착되면 검수 완료하고 새로 발송해 드린 상품 출고가 돼요 시간만 조금 양해 부탁드립니다", "evaluator_id": "senior_c", "tags": ["full_followup", "patience_request"]},
    ],
    15: [
        {"score": 10, "rationale": "반품 배송비 정확 안내 (단순 변심 2,500원) + 차감 옵션 제안 + 쿠폰 확인 시도까지 정확", "quote_example": "상담사: 반품 배송비는 이천 오백 원이 발생이 되거든요 환불받으실 금액에서 차감으로 도와드려도 될까요 ... 쿠폰이 있는지 제가 한 번 확인해 보고 말씀드리겠습니다", "evaluator_id": "senior_a", "tags": ["full_accuracy", "real_pattern", "policy_aligned"]},
        {"score": 10, "rationale": "마일리지 정책 정확 안내 (이벤트 한정 지급) + 등급별 차이 추가 정보", "quote_example": "상담사: 마일리지는 저희가 이벤트가 있을 때만 지급을 하고 있어서요 등급별 차등이 있을 수 있습니다", "evaluator_id": "senior_c", "tags": ["full_accuracy", "policy_aligned"]},
        {"score": 5, "rationale": "대략적 안내, 정확한 수치/조건 누락", "quote_example": "상담사: 한 며칠 정도 걸리실 거예요 비용은 그때 안내드릴게요", "evaluator_id": "senior_b", "tags": ["partial_accuracy"]},
        {"score": 0, "rationale": "고객이 '반송장이 두 장 있을 텐데 보관해야 하나' 라고 묻는데 잘못 안내 + 정정 없음", "quote_example": "상담사: 아 그건 그냥 버리셔도 됩니다 (실제: 환불 완료까지 보관 필요)", "evaluator_id": "senior_a", "tags": ["misinformation", "no_correction"]},
    ],
    16: [
        {"score": 5, "rationale": "택 미제거 확인 + 회수 일정 + 반송장 보관 + 검수 + 환불 일정 5요소 완벽", "quote_example": "상담사: 택 제거나 외부 착용 세탁 없이 지금 배송된 상태 그대로 ... 내일부터 기사가 이 삼 일 내에 방문 드릴 예정 ... 운송장 보관 부탁드립니다 ... 검수 이 삼 일 ... 환불은 영업일 기준 삼 일에서 오 일", "evaluator_id": "senior_a", "tags": ["all_mandatory_done", "real_pattern"]},
        {"score": 5, "rationale": "필수 5요소 + 반품/교환 동시 진행 시 송장 별도 발행 안내까지 추가", "quote_example": "상담사: 교환 상품도 회수가 필요하시잖아요 송장은 추가 발행을 하겠습니다 그것도 교환 완료되는 시점까지 보관해 주시면 됩니다", "evaluator_id": "senior_c", "tags": ["all_mandatory_done", "extra_clarification"]},
        {"score": 3, "rationale": "택 확인 + 회수 일정만 안내, 반송장 보관 / 환불 일정 누락", "quote_example": "상담사: 택만 안 떼셨으면 회수 가능하시고요 기사가 갈 거예요", "evaluator_id": "senior_b", "tags": ["mandatory_partial"]},
    ],
    17: [
        {"score": 5, "rationale": "본인 확인 사유 ('소중한 정보 확인') + 정중한 정보 요청 + 의문형 재확인 + 감사 표현 4단계 완벽", "quote_example": "상담사: 본인 확인을 위해 성함과 휴대폰 번호를 말씀해 주시겠습니까 ... 예 OOO 고객님 본인 맞으십니까 ... 예 소중한 정보 확인 감사합니다", "evaluator_id": "senior_a", "tags": ["full_pii_procedure", "real_pattern", "thanks_after"]},
        {"score": 5, "rationale": "민감 정보 마스킹 처리된 발화 + 본인 확인 절차 정중", "quote_example": "상담사: 소중한 정보 보호를 위해서 성함이랑 휴대폰 번호 같이 말씀 부탁드리겠습니다 ... OO 고객님 본인 맞으십니까", "evaluator_id": "senior_c", "tags": ["full_pii_procedure", "privacy_emphasis"]},
    ],
    18: [
        {"score": 5, "rationale": "고객 정보 발화 시 마스킹 (***) + 본인확인 후 PII 노출 없이 진행", "quote_example": "상담사: 예 *** 고객님 본인 맞으십니까", "evaluator_id": "senior_a", "tags": ["full_compliance", "real_pattern", "masked_pii"]},
        {"score": 5, "rationale": "전사 전체에서 PII 풀로 노출 없이, 부분 표시만 사용", "quote_example": "상담사: OOO 고객님 등록된 010-XXXX-XXXX 번호로 SMS 발송됩니다", "evaluator_id": "senior_c", "tags": ["full_compliance", "partial_disclosure"]},
    ],
}


def main() -> int:
    if not DIR.is_dir():
        print(f"[error] {DIR} 없음")
        return 1
    total_added = 0
    for item_no, new_recs in ADDITIONS.items():
        files = sorted(DIR.glob(f"{item_no:02d}_*.json"))
        if not files:
            continue
        path = files[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[error] {path.name}: {e}")
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
    print(f"\n[done] 실제 패턴 기반 추가: {total_added} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
