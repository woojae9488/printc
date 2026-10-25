# mks-gen-l

## 부품

- 보드: MKS Gen L v1.0 (ATmega2560, 16MHz), USB로 Pi Zero 2 WH 직결.
    - CH340이라 `usb-1a86_USB_Serial...`로 보이고 인식은 `dmesg | grep ch341`.
- 드라이버: DRV8825 x5 (1/32, 점퍼 3개). 전류 = Vref × 2
- 피스톤: SL 모터 + T8 로드 일체(리드 8), E0 단자, 주사기 60 mL 안지름 26.7
- 펌웨어: 아래 펌웨어 빌드.
- 배포: Pi `~/printer_data/config/`에 저장소를 링크.
    - `printer.cfg`는 `[include <링크>/hardware/active/printer.cfg]` 한 줄.
    - 파일 단위 복사, 링크 금지(cream.cfg include 상대 경로가 깨짐).
    - `[pause_resume]` 사용, Mainsail `mainsail.cfg` include 금지(RESUME, CANCEL_PRINT 충돌).

## 펌웨어 빌드

Klipper 소스 디렉터리에서 수행. 생성된 `out/klipper.dict`는 `@sim/run.sh` 모의 실행에도 씀.

1. `@hardware/mks-gen-l/firmware.config`를 Klipper 소스의 `.config`로 복사.
2. `make olddefconfig`.
    - `make menuconfig`에서 Atmega AVR, atmega2560, 16Mhz인지 확인.
3. `make clean && make`. `out/klipper.dict` 생성.
4. 모의 실행은 `KLIPPER_DICT` 기본 경로 `$KLIPPER_DIR/out/klipper.dict`를 읽음.
    - 다른 위치면 `KLIPPER_DICT=<경로>` 지정.
5. 실기 올리기: `sudo service klipper stop` 후 `make flash FLASH_DEVICE=<[mcu] serial>`.
    - 끝에 `avrdude done`이 나오면 성공. 이후 `sudo service klipper start`.
    - 실패 시 `@docs/troubleshooting.md`.

## 모터 단자, Vref (V)

| 단자 | 모터                    | Vref |
|------|-------------------------|------|
| X    | X                       | 0.40 |
| Y    | Y                       | 0.50 |
| Z    | Z 왼쪽                  | 0.30 |
| E1   | Z 오른쪽 (`stepper_z1`) | 0.30 |
| E0   | 피스톤                  | 0.50 |

병렬 대안.

- Z 두 모터를 Z 단자에 병렬, Vref 0.60, `[stepper_z1]` 삭제.
- 드라이버 4개로 충분.
- 분리, 병렬 중 사용자 결정 필요.

## 보정 기록

| 날짜 | 항목 | 전  | 후  |
|------|------|-----|-----|

## 기준 시간

3호 케이크 498초(첫 가이드의 SKR 기준 Klipper 모의 실행 값).

## 검증 필요

- 기준 시간 498초: SKR 기준 값. 이 프로필(MKS Gen L)로 모의 실행해 갱신.
- `serial`: 보드마다 `ls /dev/serial/by-id/`로 확인.
- `[stepper_z1]` 핀(E1: PC1, PC3, !PC7), `dir_pin`, Vref 0.30: 가이드에 없음.
    - 드라이버 5번째 추가 구매 필요.
- 가이드에 없음.
    - `printer.cfg`: `max_z_velocity`, `max_z_accel`, `homing_speed`, `nozzle_diameter`.
    - `cream_vars.cfg`: `variable_travel_speed`, `variable_print_speed`.
- `variable_prime_mm` 0.36(가이드 프라이밍 0.2 mL 환산): 실기 확인.
- `variable_swap_lift` 15(가이드 '케이크 위 15 mm' 이동 높이 차용): 실기 확인.
    - 교체 높이 규칙은 `@docs/calibration.md` 교체 자리.
- `variable_piston_speed` 5: 임시값. 기본 `max_extrude_only_velocity` 이내.
    - 기본값 ≈ `max_velocity` × 4 × `nozzle_diameter`² ÷ 주사기 단면적 ≈ 6.7.
- `variable_piston_max` 90: 가이드 '피스톤 0–90 mm'.
- `variable_line_width` 4, `variable_line_height` 2.5, `variable_gap` 3: 도구 기본값.
    - 가이드 '줄 굵기 2.5 mm', '깍지 끝은 케이크 윗면 1 mm 위'와 대조.
- Z 3200 step/mm 급 AVR 스텝 속도: 모의 실행에서 `Timer too close` 확인.
- 모든 `dir_pin`의 `!`: 첫 전원 방향 시험에서 확정.
- `firmware.config` 항목 이름: `make menuconfig` 화면과 대조.
    - `make olddefconfig` 후 `.config`에 값이 그대로 남는지 확인.
- Pi 밖에서 빌드 시 AVR 빌드 도구(avr-gcc 등) 별도 설치 필요 여부.
    - 가이드는 Pi에 Klipper 설치 시 함께 들어온다고만 기재.
