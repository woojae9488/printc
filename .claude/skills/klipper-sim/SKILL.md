---
name: klipper-sim
description: Klipper 모의 실행으로 printer.cfg, 매크로, gcode 검증. cfg, 매크로, tools 변경 후 또는 동작 확인 요청 시 사용.
paths:
  - hardware/**
  - klipper/**
  - tools/**
  - sim/**
---

# klipper-sim

모의 실행만 수행.

1. `tools/`로 시험 문구 gcode 생성.
2. `sim/run.sh` 실행. 입력은 `hardware/active/printer.cfg`와 생성한 gcode.
3. 출력에서 `Error`, `Move out of range`, `Unknown command` 확인.
4. 예상 시간을 `hardware/active/NOTES.md`의 기준 시간과 비교해 보고.
5. 실패 시 klippy 로그 마지막 20줄 요약.

`sim/run.sh`가 없으면 중단하고 보고.
