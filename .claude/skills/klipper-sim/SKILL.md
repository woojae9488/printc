---
name: klipper-sim
description: >-
  Klipper 모의 실행으로 printer.cfg, 매크로, gcode 검증.
  cfg, 매크로, 도구 변경 후 또는 동작 확인 요청 시 사용.
paths:
  - hardware/**
  - klipper/**
  - tools/**
  - sim/**
---

# klipper-sim

모의 실행만 수행.

1. `@tools/cream_text.py`로 시험 문구 gcode를 `out/job.gcode`에 생성.
    - 인자는 `python3 tools/cream_text.py -h`.
2. `KLIPPER_DIR=<klipper 경로> sim/run.sh out/job.gcode [프로필]` 실행. 사용법은 `sim/run.sh -h`.
3. 종료 코드(0 성공, 1 오류, 2 인자·환경)와 출력(예상 시간 차이, 실패 시 로그 요약) 보고.

`@sim/run.sh`가 없으면 중단하고 보고.
