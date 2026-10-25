# printc

생크림 레터링 케이크용 3D 프린터 솔루션. 스택은 Klipper + Moonraker + Mainsail.

## 구조

- `hardware/<프로필>/`: 하드웨어 종속 파일(구성은 `hardware/README.md`)
- `hardware/active`: 현재 프로필 링크
- `klipper/macros/`: 하드웨어 무관 매크로
- `tools/`: 글씨 → gcode 변환 등 스크립트
- `sim/`: Klipper 모의 실행
- `docs/`: 조립, 보정, 문제 해결 절차
