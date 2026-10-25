---
paths:
  - hardware/**
  - klipper/**
  - tools/**
  - sim/**
  - docs/**
---

# 하드웨어 분리

- 기계 값(범위, 핀, 전류, 주사기 치수, 위치)은 `@hardware/active/` 안에만 둠.
- 매크로, 도구, 문서에 기계 값 직접 기재 금지. 변수나 cfg 참조.
- 변경 후 `/klipper-sim`으로 검증.
- 하드웨어 교체, 보정값 반영은 `/hw-profile` 사용.
