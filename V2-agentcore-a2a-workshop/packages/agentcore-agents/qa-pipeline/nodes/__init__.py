# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V1 legacy node package — V2 가 참조하는 공용 모듈만 잔존.

V2 전환 완료 후 V1 노드 파일 (greeting, understanding, courtesy, mandatory,
scope, proactiveness, work_accuracy, incorrect_check, orchestrator,
consistency_check, score_validation, report_generator, retrieval,
wiki_compiler, sample_data) 은 삭제됨. 다음 모듈만 V2 가 import 할 수 있도록 잔존:

- ``nodes.skills`` (pattern_matcher, reconciler, constants, scorer 등)
- ``nodes.dialogue_parser`` (V2 Layer1 segment_splitter 가 내부 함수 사용)
- ``nodes.llm`` (Group A/B Sub Agent 가 Bedrock 호출 시 사용)
- ``nodes.qa_rules`` (skills.reconciler/scorer 가 사용)
- ``nodes.json_parser`` (nodes.llm 이 사용)

Import side-effect 를 피하기 위해 여기서 서브모듈을 eager import 하지 않는다.
각 서브모듈은 참조 시점에 개별 import 하여 사용할 것.
"""

__all__: list[str] = []
