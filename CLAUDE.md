# printc

생크림 레터링 케이크용 3D 프린터 솔루션. 스택은 Klipper + Moonraker + Mainsail.

## 구조

- `@hardware/<프로필>/`: 하드웨어 종속 파일(구성은 `@hardware/README.md`)
- `@hardware/active`: 현재 프로필 링크
- `@klipper/macros/`: 하드웨어 무관 매크로
- `@klipper/moonraker.conf`: 하드웨어 무관 Moonraker 설정
- `@tools/`: 글씨 → gcode 변환 등 스크립트
- `@sim/`: Klipper 모의 실행
- `@deploy/`: Pi 설정 반영, MCU 펌웨어 업로드 스크립트
- `@docs/`: 배포, 첫 전원, 보정, 레터링, 문제 해결 절차

## 참조 규칙

- 파일, 디렉터리: `@경로`. 루트 기준, 백틱 필수.
- 스킬: `/이름`. 기본 스킬 포함.
- 서술어("참조", "따름")는 필요할 때만 붙임.
