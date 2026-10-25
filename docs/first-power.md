# 첫 전원

조립 후 처음 전원을 넣고 방향, 스위치, 원점, 범위를 하나씩 확인하는 절차.

- 케이크 판은 비우고 카트리지는 뺌.
- 명령은 Mainsail Console에 입력.
- 손은 비상 정지(EMERGENCY STOP 또는 전원 스위치) 위에 둠.
- 이상이 생기면 멈추고 `@docs/troubleshooting.md` 확인.

## 드라이버 주의

- 24 V가 켜진 상태에서 모터 커넥터를 뽑거나 꽂지 않음. 드라이버가 파손됨.
- 드라이버마다 방열판을 붙이고 박스 팬(`[controller_fan box]`)이 도는지 확인.
- 마이크로스텝은 점퍼로 정함.
    - 점퍼 설정과 `@hardware/active/printer.cfg`의 각 `[stepper_*] microsteps`가 같아야 함.
- 전류는 Vref로 정함. 환산식과 목표 Vref는 `@hardware/active/NOTES.md`.

## 절차

1. 전원 넣기 전 자세
    - 갠트리는 가운데 높이, 판과 헤드는 가운데.
    - 밀대는 표시선(맨 위)에 맞춤.
    - 피스톤 스위치가 없어 켜질 때의 밀대 자리가 원점이 됨.
2. Vref 조정
    - 모터 커넥터를 모두 뺀 채 보드만 켬.
    - 드라이버마다 가변저항 중앙과 GND 사이 전압을 측정.
    - 목표와 다르면 절연 드라이버로 0.05 V씩 돌려 맞춤.
    - 전원을 끈 뒤 모터 연결.
3. Mainsail 확인
    - 전원을 켜고 `Klipper ready`와 extruder 온도(실온 근처)가 보이는지 확인.
    - 아니면 `klippy.log`의 첫 오류 줄 확인.
4. 엔드스톱 읽기
    - `QUERY_ENDSTOPS`를 스위치를 안 누른 채, 손으로 누른 채 한 번씩 실행.
    - `open` → `TRIGGERED`가 정상.
    - 반대면 C–NC 배선 확인. 그래도 반대면 해당 `endstop_pin` 앞 `!` 추가 또는 제거.
5. 축별 방향
    - 아래 명령마다 1 mm 갔다 돌아옴. 반대면 해당 섹션 `dir_pin` 앞 `!` 추가 또는 제거.
    - `STEPPER_BUZZ STEPPER=stepper_x`: 헤드가 오른쪽(+X)
    - `STEPPER_BUZZ STEPPER=stepper_y`: 판이 앞(사람 쪽)
    - `STEPPER_BUZZ STEPPER=stepper_z`: 갠트리가 위로
    - `STEPPER_BUZZ STEPPER=stepper_z1`: 오른쪽 스크루가 `stepper_z`와 같은 방향(위로)
    - `STEPPER_BUZZ STEPPER=extruder`: 밀대가 아래로
    - `stepper_z`, `stepper_z1` 단독 버즈는 한쪽 스크루만 돌려 갠트리를 비틂.
        - 반복 실행 금지, 끝난 뒤 빔 수평 확인.
6. 드라이버 응답
    - `SET_STEPPER_ENABLE STEPPER=<섹션 이름> ENABLE=1`로 모든 모터를 켠 채 1분 둔 뒤 방열판을 만짐.
    - 손을 댈 수 있으면 정상.
    - 뜨거우면 2번 절차로 그 드라이버 Vref를 0.05 V 낮춤.
    - 높은 소리가 계속 나면 코일 쌍 재확인.
7. 원점 `G28`
    - Z가 위 스위치까지 오른 뒤 X, Y 순으로 원점을 잡음.
    - 순서는 `@hardware/active/printer.cfg` `[homing_override]`.
    - 각 축이 스위치 쪽으로 가서 멈추는지 확인.
    - 반대로 가거나 스위치를 지나치면 즉시 비상 정지.
8. 범위
    - X, Y를 각 `[stepper_x]`, `[stepper_y]`의 `position_max`까지 보냈다가 `G1 X0 Y0`.
    - 끝까지 가도 아무것에도 닿지 않아야 함.
9. Z
    - Z를 천천히 작은 값까지 내림.
    - 판에 닿을 듯하면 멈추고 `@docs/calibration.md`의 Z 영점 수행.
10. 피스톤
    - `CREAM_PISTON DIST=10` → `CREAM_PISTON DIST=-10`. 밀대가 내려갔다 올라와야 함.
    - 표시선에서 어긋나면 `CREAM_PISTON DIST=<거리> FORCE=1`로 맞추고 `CREAM_PISTON_SET`.
11. 교체 자리
    - 카트리지 없이 `T0`.
    - 헤드가 교체 자리(높이 규칙은 `@docs/calibration.md` 교체 자리)로 가서 멈춤.
    - 알림이 뜨면 `RESUME`.
12. 비상 정지 연습
    - `M112` → `FIRMWARE_RESTART`.
