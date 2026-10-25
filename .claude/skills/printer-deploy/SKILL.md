---
name: printer-deploy
description: >-
  Pi 설정 반영과 MCU 펌웨어 업로드.
  실기 반영 요청 시 사용.
---

# printer-deploy

사용자가 요청하고 확인한 경우에만 수행.

사용법은 `@deploy/sync.sh`, `@deploy/flash.sh` `-h`.

1. cfg, 매크로, 도구 변경이 있으면 `/klipper-sim` 통과 확인. 실패 시 중단.
2. 대상 호스트(`PI_HOST`)를 사용자에게 확인. 값은 추측하거나 기록하지 않음.
3. `deploy/sync.sh -n` 미리보기 결과 보고. 확인 후 `deploy/sync.sh` 실행.
    - 최초 1회만 `--init` 추가.
4. `@docs/deploy.md` MCU 펌웨어의 조건일 때만 펌웨어 업로드.
    - `deploy/flash.sh -n` 요약 보고. 확인 후 `deploy/flash.sh --yes` 실행.
    - tty가 없어 `--yes`가 필요함. 사용자 명시 확인 없이 붙이지 않음.
5. 종료 코드(0 성공, 1 원격 작업 실패, 2 인자·환경 오류)와 출력 보고.
    - 이후 `@docs/deploy.md` 확인 절을 거쳐 `@docs/first-power.md`.

절차 상세는 `@docs/deploy.md`, 안전 확인은 `@.claude/rules/safety.md`.
