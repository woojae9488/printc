# 배포

Pi에 Klipper 호스트와 설정, MCU에 펌웨어를 올리는 절차.

- 스크립트는 로컬 저장소 루트에서 실행. 인자, 동작은 각 스크립트 `-h`.
- 이상이 생기면 멈추고 `@docs/troubleshooting.md` 확인.

종료 코드.

- 0: 성공.
- 1: 원격 작업 실패. 인쇄 중, 인쇄 상태 조회 실패로 인한 중단 포함.
- 2: 인자·환경 오류. ssh 접속 실패(255), 확인 질문 거부, 로컬 tar 실패 포함.

## 개요

| 대상  | 올리는 것                        | 수단                |
|-----|------------------------------|-------------------|
| Pi  | Klipper, Moonraker, Mainsail | KIAUH(1회)         |
| Pi  | 프로필, 매크로                     | `deploy/sync.sh`  |
| MCU | MCU 펌웨어                      | `deploy/flash.sh` |

버전 일치.

- Pi `PI_KLIPPER` 체크아웃은 `@sim/Dockerfile` `KLIPPER_REF`와 같게 둠.
    - 모의 실행과 실기가 같은 Klipper를 씀.
- 호스트와 MCU 펌웨어의 명령 형식이 다르면 mcu 연결 오류가 날 수 있음. 검증 필요.
- `KLIPPER_REF` 변경 순서: `@sim/Dockerfile` 수정 → `/klipper-sim` → Pi 체크아웃 → MCU 펌웨어.

## Pi 최초 설치

1회만 수행. KIAUH가 대화형이라 스크립트화하지 않음.

1. OS 준비
    - Raspberry Pi Imager로 Raspberry Pi OS Lite를 기록. Pi 모델은 `@hardware/active/NOTES.md` 부품.
    - Imager 설정에서 호스트 이름, 사용자, Wi-Fi, SSH를 켬.
    - 32, 64비트 선택은 검증 필요.
2. SSH
    - `ssh <사용자>@<호스트>` 접속 확인. 이 주소가 `PI_HOST`.
    - `ssh-copy-id <사용자>@<호스트>`로 키 등록. 스크립트가 비밀번호 없이 접속하도록 함.
3. KIAUH
    - `git clone https://github.com/dw-0/kiauh.git` 후 `./kiauh/kiauh.sh`. 저장소 주소는 검증 필요.
    - 메뉴에서 Klipper, Moonraker, Mainsail 순으로 설치.
    - 이후 KIAUH로 Klipper를 업데이트하지 않음. 체크아웃이 바뀜.
4. Klipper 버전
    - `git -C ~/klipper fetch --tags` 후 `git -C ~/klipper checkout <KLIPPER_REF>`.
    - `~/klippy-env/bin/pip install -r ~/klipper/scripts/klippy-requirements.txt`.
        - 가상 환경 경로는 KIAUH 기본값 기준. 검증 필요.
    - `sudo service klipper restart`.
5. 브라우저로 `http://<호스트>` 접속, Mainsail 화면 확인.
    - MCU 펌웨어 전이라 mcu 연결 오류는 정상.

## 설정 반영

로컬 우선 단방향.

- 설정은 로컬 저장소 파일을 고치고 `deploy/sync.sh`로 Pi에 반영.
- Mainsail에서 Pi 사본을 직접 고치면 다음 반영 때 덮임.

환경 변수. `$HOME`은 Pi 사용자 홈.

| 변수           | 기본                          | 뜻                    |
|--------------|-----------------------------|----------------------|
| `PI_HOST`    | 없음(필수)                      | SSH 주소 `<사용자>@<호스트>` |
| `PI_REPO`    | `$HOME/printc`              | Pi의 저장소 사본           |
| `PI_CONFIG`  | `$HOME/printer_data/config` | Klipper 설정 디렉터리      |
| `PI_KLIPPER` | `$HOME/klipper`             | Klipper 소스           |

- 경로 변수는 Pi 기준 절대 경로로 지정. 아니면 종료 2.
    - `~`는 로컬 셸이 로컬 홈으로 펼치므로 쓰지 않음.

설정 디렉터리 배치.

- `PI_CONFIG/printc`는 `PI_REPO`를 가리키는 링크.
- `PI_CONFIG/printer.cfg`는 `[include printc/hardware/active/printer.cfg]` 한 줄.
- 프로필 파일을 `PI_CONFIG`에 파일 단위로 복사, 링크하지 않음.
    - `cream.cfg` include 상대 경로가 깨짐.
- `[pause_resume]`는 프로필에서 정의. Mainsail `mainsail.cfg` include 금지.
    - `RESUME`, `CANCEL_PRINT`가 충돌함.

절차.

1. 최초 1회 `deploy/sync.sh --init`.
    - 전송 후 `PI_CONFIG/printc` 링크를 만듦.
    - `printer.cfg`가 없을 때만 include 한 줄로 생성.
    - 있으면 덮어쓰지 않고 교체할 한 줄을 안내.
2. `deploy/sync.sh -n`으로 보낼 파일 미리보기.
3. `deploy/sync.sh` 실행. 전송 후 Moonraker API로 Klipper 재시작.
    - 인쇄 중(`printing`, `paused`)이면 전송 전에 중단하고 종료 1.
    - 로컬에서 전부 묶은 뒤 보냄. 묶기 실패 시 Pi는 바뀌지 않음.
    - `--no-restart`는 재시작과 인쇄 중 확인을 생략.

한계.

- 추적 파일(`git ls-files`)만 보냄. 새 파일은 `git add` 후 반영.
- 미커밋 변경은 작업 트리 내용 그대로 보냄. Pi 사본은 git remote가 필요 없음.
- 로컬에서 지운 파일은 Pi에서 지우지 않음.
- Moonraker가 Pi `localhost:7125`에서 응답한다고 가정.
    - 인쇄 상태 조회가 실패(401, 503, 시간 초과 등)하면 전송 전에 중단하고 종료 1.
    - 그래도 보내려면 `--no-restart`. 재시작은 직접 수행.

## MCU 펌웨어

처음, 보드 교체, `firmware.config` 변경, `KLIPPER_REF` 변경 때만 올림.

1. `deploy/sync.sh`로 프로필을 먼저 반영.
2. `deploy/flash.sh -n [프로필]`로 미리보기. 프로필 기본은 `hardware/active`.
    - 로컬 프로필의 `firmware.config`, `printer.cfg` `[mcu] serial`을 읽음. include는 따라가지 않음.
3. `deploy/flash.sh [프로필]` 실행.
    - 실행 전 `[y/N]` 확인을 물음. `--yes`는 확인 생략.
    - tty가 없으면 확인을 물을 수 없어 `--yes` 필요.
    - 수동 절차, 성공 판정은 `@hardware/active/NOTES.md` 펌웨어 빌드.
4. 실패 시
    - klipper 서비스가 멈춰 있으면 `ssh <PI_HOST> sudo service klipper start`.
    - 원인 조치는 `@docs/troubleshooting.md` `make flash` 실패.

원격 단계. Pi `PI_KLIPPER`에서 수행.

1. 체크아웃이 `KLIPPER_REF`와 같은지 확인. 다르면 중단.
2. 시리얼 장치, 비밀번호 없는 `sudo`, 인쇄 상태 확인.
    - 인쇄 중이거나 조회 실패면 중단하고 종료 1. klipper를 멈추지 않음.
3. `firmware.config`를 `.config`로 전송.
4. `make olddefconfig && make clean && make`.
5. `sudo service klipper stop` → `make flash` → `sudo service klipper start`.
    - 업로드 방식(`FLASH_DEVICE` 시리얼)은 프로필 종속. `@hardware/active/NOTES.md` 펌웨어 빌드.
    - 정지 이후 실패, 신호(HUP, INT, TERM)에도 시작 수행.

## 확인

- Mainsail Console에 `Klipper ready`. 표시 문구는 검증 필요.
- Mainsail 시스템 정보의 Klipper 버전, mcu 버전이 `KLIPPER_REF`와 같은지 확인.
    - 화면 위치는 검증 필요.
- 다음은 `@docs/first-power.md`.

## 안전

- Pi 배포, MCU 업로드는 사용자 확인 후 수행(`@.claude/rules/safety.md`).
- 작업 중에는 반영, 업로드하지 않음. Klipper 재시작으로 작업이 멈춤.
- 업로드 중 보드 전원, USB를 빼지 않음.
- 모터 커넥터 주의는 `@docs/first-power.md` 드라이버 주의.
