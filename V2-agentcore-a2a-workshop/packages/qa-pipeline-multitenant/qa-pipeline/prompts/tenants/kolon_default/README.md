# `kolon_default` 테넌트 프롬프트 오버라이드

기본 테넌트(코오롱) 전용 프롬프트를 이 디렉토리에 두면 공통 프롬프트보다 우선 로드된다.

## 경로 규칙

로더(`prompts/__init__.py`)는 아래 순서로 파일을 찾는다.

1. `prompts/tenants/kolon_default/{name}.sonnet.md`  (테넌트 오버라이드, .sonnet 우선)
2. `prompts/tenants/kolon_default/{name}.md`         (테넌트 오버라이드, .md 폴백)
3. `prompts/{name}.sonnet.md`                        (기본, .sonnet 우선)
4. `prompts/{name}.md`                               (기본, .md 폴백)

모두 없으면 `FileNotFoundError`.

시그니처: `load_prompt(name: str, *, tenant_id: str, include_preamble: bool = True, backend: str | None = None) -> str`
— `tenant_id` 는 keyword-only, `backend` 는 예약(현재 미사용).

## 오버라이드 파일 작성 규칙

- 파일명 규칙은 `{item_key}.sonnet.md`. 확장자는 반드시 `.sonnet.md`.
- 예: `item_04_empathy.sonnet.md`, `task_planner.sonnet.md`, `report_generator.sonnet.md`
- 상단에 YAML front matter(`---\n...\n---\n`) 를 둬도 로더가 벗겨낸다.
- `_common_preamble.sonnet.md` 는 로더가 자동 prepend 하므로 오버라이드 본문에 중복 포함하지 말 것.
- `_common_preamble.sonnet.md` 자체도 테넌트별로 재정의 가능 — 동명 파일을 이 폴더에 두면 기본 preamble 을 대체한다.

## 현재 오버라이드 목록

(이 폴더에 파일이 없으면 전부 기본 프롬프트 사용.)

## 새 오버라이드 추가 절차

1. 기본 파일 복사
   ```bash
   cp qa-pipeline/prompts/item_16_mandatory_script.sonnet.md \
      qa-pipeline/prompts/tenants/kolon_default/item_16_mandatory_script.sonnet.md
   ```
2. 테넌트 특화 내용(필수 멘트, 고유 상품명 등)을 편집.
3. 유닛 테스트
   ```python
   from prompts import load_prompt, clear_cache
   clear_cache()
   text = load_prompt("item_16_mandatory_script", tenant_id="kolon_default")
   assert "kolon" in text or "코오롱" in text   # 혹은 기대하는 커스텀 마커
   # 자체 출력 규칙이 있는 프롬프트는 preamble 비활성화
   text2 = load_prompt("report_generator", tenant_id="kolon_default", include_preamble=False)
   ```
4. 로더 캐시 무효화 필요 시 `prompts.clear_cache()` 호출 (런타임 핫리로드용).

## 참조

- `ARCHITECTURE.md` 7절 (프롬프트 오버라이드 로더)
- `docs/TENANT_CONFIG.md` (신규 테넌트 온보딩 전체 절차)
