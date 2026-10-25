---
name: hw-profile
description: 하드웨어 교체(보드, 모터, 주사기, 프레임) 또는 보정값 반영 시 hardware/ 프로필 생성, 수정.
paths:
  - hardware/**
  - docs/**
---

# hw-profile

## 새 프로필

1. 현재 프로필 복사: `@hardware/<새이름>/`.
2. 변경 항목만 수정(구성은 `@hardware/README.md`).
    - 보드: 핀, 펌웨어 설정
    - 모터: 전류, rotation_distance
    - 주사기: 필라멘트 지름, 장전 거리
    - 프레임: 이동 범위, 교체 자리, 컵 위치
3. `@hardware/active` 링크 교체.
4. `/klipper-sim` 실행.

## 보정

1. 측정값 수신.
2. `@docs/calibration.md` 공식으로 계산.
3. `@hardware/active/` 아래 cfg 수정.
4. `@hardware/active/NOTES.md` 보정 기록에 한 줄 추가(날짜, 항목, 전, 후).
5. `/klipper-sim` 실행.
