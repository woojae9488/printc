# 보정

측정값으로 cfg 항목을 고치는 공식과 순서.

- 순서: 이동 거리 → 빔 수평 → Z 영점 → 컵, 교체 자리 → 크림 양 → 장전 거리 → 첫 획.
- 기호: `cfg` = `@hardware/active/printer.cfg`, `vars` = `@hardware/active/cream_vars.cfg`.
- 값을 바꿀 때마다 저장 후 `RESTART`.
    - Pi는 저장소 링크를 읽으므로 Mainsail에서 고친 값이 저장소에 바로 반영됨.
    - 배포 방식은 `@hardware/active/NOTES.md`.
- 반영과 기록은 `/hw-profile` 보정 절차로 수행.

## 이동 거리

1. `G28` 후 헤드(또는 판)에 표시하고 지시 거리 D만큼 이동. 강철자로 실제 거리 측정.
2. 허용 ±0.3 mm. 벗어나면 다음으로 고침.

| 축  | 측정                             | 항목                                                    |
|-----|----------------------------------|---------------------------------------------------------|
| X   | `G1 X<D>`                        | `cfg` `[stepper_x] rotation_distance`                   |
| Y   | `G1 Y<D>`                        | `cfg` `[stepper_y] rotation_distance`                   |
| Z   | 두 Z 높이 사이 판 아랫변 높이 차 | `cfg` `[stepper_z]`, `[stepper_z1]` `rotation_distance` |

```
rotation_distance_new = rotation_distance_old × 지시 ÷ 실제
```

피스톤 `[extruder] rotation_distance`는 바꾸지 않음. 양은 크림 양에서 조정.

## 빔 수평

1. 빈 카트리지(깍지 달린)를 끼우고 Z를 판 가까이 내림.
2. 헤드를 X 양 끝 근처 두 곳에 두고 깍지 끝과 판 사이를 틈새 게이지로 측정.
3. 차가 0.2 mm를 넘으면 `M84` 후 높은 쪽 커플러를 풀고 그쪽 스크루만 돌림.

```
스크루 회전각(°) = 높이 차 ÷ [stepper_z] rotation_distance × 360
```

## Z 영점

1. `G28` 후 `vars` `variable_center_x`, `variable_center_y` 위로 이동, Z를 작게 내림.
    - Mainsail Z offset은 0.
2. 판 위에 종이 한 장을 놓고 Z를 0.1 mm씩 내림.
    - 종이가 살짝 끌리는 높이의 화면 Z를 g로 둠.
3. 다음 두 항목을 같은 값으로 고침. 이후 Z 0 = 깍지 끝이 판에 닿는 높이.

```
position_endstop_new = position_endstop_old − g
position_max = position_endstop_new
```

항목: `cfg` `[stepper_z] position_endstop`, `[stepper_z] position_max`.

## 프라이밍 컵

1. Z를 올린 뒤 `vars` `variable_prime_x`, `variable_prime_y`로 이동, `variable_prime_z`로 내림.
2. 깍지가 컵 가운데, 테두리보다 위에 와야 함. 어긋나면 해당 변수 조정.
3. `PRIME`이 짜는 양은 `vars` `variable_prime_mm`.

## 교체 자리

1. 빈 카트리지를 끼운 채 `T1`.
2. 주사기를 앞으로 끝까지 당겨 빼도 판, 틀에 닿지 않고 손이 들어가야 함.
3. 불편하면 `vars` `variable_swap_x`, `variable_swap_y`, `variable_swap_z` 조정.

교체 높이 규칙.

- `variable_swap_z`와 현재 Z + `variable_swap_lift` 중 높은 쪽(Z 최대 이내).
- Z는 올리기만 하므로 이미 높으면 그 높이에서 교체.

## 크림 양

1. 밀도 측정: ρ = 일정 부피 크림 무게 ÷ 부피.
2. `M221 S100`으로 Extrusion factor를 100으로 되돌림.
3. `T0` 실행 후 카트리지를 끼우고 `RESUME`, `PRIME` 후 저울 위 접시에 `M83` → `G1 E<L> F60`.
4. 실제량 = 무게 ÷ ρ.
    - 기대량과 비교해 Mainsail Extrusion factor(`M221 S<값>`)를 고침.

```
주사기 단면적(mm²) = π × (filament_diameter ÷ 2)²
1 mm 압출량(mL) = 주사기 단면적 × 1 mm ÷ 1000
기대량(mL) = 1 mm 압출량 × L
Extrusion factor = 100 × 기대량 ÷ 실제량
```

항목: `cfg` `[extruder] filament_diameter`(주사기 안지름).

## 장전 거리

밀대가 플런저에 닿기 전에 짜기 시작하거나, 너무 눌러 튀면 `vars` `variable_load_mm` 조정.

```
load_mm_new = load_mm_old + 부족한 거리
1 mL당 밀대 이동(mm) = 1000 ÷ 주사기 단면적
```

## 첫 획

시험판에 `@tools/cream_text.py`로 만든 문구를 짜며 조정.

| 증상             | 조정                                            |
|------------------|-------------------------------------------------|
| 줄이 뜸          | Mainsail Z offset −0.2씩                        |
| 줄이 눌림        | Mainsail Z offset +0.2씩                        |
| 줄이 가늚        | Extrusion factor 5 %씩 올림                     |
| 획 시작, 끝 뭉침 | `cfg` `[extruder] pressure_advance` 0.05씩 올림 |

- 획 시작, 끝 뭉침은 뭉침이 줄어드는 값을 선택.
