# kolon 인바운드 기본 부서 (default)

`department` 필드가 지정되지 않은 요청이 도착했을 때 떨어지는 fallback 버킷입니다.

운영 가이드:
- 이 폴더는 **비워 두는 것이 권장**. 실제 데이터는 상위 `_shared` 또는 레거시 `tenants/kolon/` 위치에서 fallback 됩니다.
- 특정 부서로 분류되지 않은 "기타" 유입을 위한 default 자원만 한정적으로 둡니다.
- 부서가 확정되면 `tenants/kolon/inbound/{department_id}/` 에 별도 폴더 생성.

## tenant_config.yaml (선택)

이 경로에 `tenant_config.yaml` 을 두면 default 부서용 파라미터 override 가 가능합니다.
파일이 없으면 상위 레이어(`_shared` → site → generic) 의 config 가 자동 적용됩니다.
