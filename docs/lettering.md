# 레터링

문구와 폰트로 글씨 gcode를 만드는 방법.

- 도구: `@tools/cream_text.py`. 전체 인자는 `-h`.
- 기호: `vars` = `@hardware/active/cream_vars.cfg` `[gcode_macro _CREAM_CFG]`.

## 설치

모든 실행에 필요. 실행은 `.venv/bin/python`으로 수행.

```
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
```

- 의존 없이 실행하면 위 설치를 안내하고 종료.

## 사용법

```
.venv/bin/python tools/cream_text.py "생일 축하해" --out out/job.gcode --preview out/job.svg
```

- 인자 하나가 한 줄. 줄마다 가운데 정렬.
- 글씨는 항상 폰트 윤곽선으로 그림. 폰트에 없는 글자는 오류.
- `--font`: 패밀리 이름 또는 `.ttf`, `.otf` 경로. 생략 시 `DEFAULT_FONT`(`@tools/cream_text.py`).
    - 이름은 `@tools/fontsrc.py`가 해석. 캐시에 없으면 google/fonts에서 최초 1회 내려받음.
    - google/fonts의 폰트는 각 라이선스(대부분 OFL)를 따름.
    - 캐시: `~/.cache/printc/fonts`. `PRINTC_FONT_CACHE`로 바꿈. 저장소 안은 거부.
    - TTC(폰트 모음) 미지원.
- `--weight`: 가변 폰트 `wght` 축 값. 가변이 아닌 폰트에 쓰면 오류.
    - 생략 시 기본 폰트는 `DEFAULT_WEIGHT`, `--font`로 준 폰트는 폰트 기본 굵기.
- `--style`: `fill`(기본, 안쪽까지 선으로 채움), `outline`(윤곽 한 바퀴).
- `--fallback`: 가는 획 처리. 아래 폴백 사다리.
- `--allow-thin`: 폴백으로도 못 그리는 글자를 건너뛰고 경고.
- `--preview`: 짜는 경로 SVG. 저장소 안 `.svg`만 허용.
    - 폴백 획은 주황, 이동은 회색 점선.
- 생성 메시지에 폴백 요약 출력. 예: `폴백: embolden 3자, center 1자`.

## 크기

- `--char-height`: em 높이.
    - em은 폰트 설계 격자 높이. 실제 글자 몸은 em보다 작음.
- 생략 시 케이크 안에 들어가는 최대 크기. 상한은 `AUTO_CHAR_HEIGHT_CAP`.
- 생성 메시지에 실제 글자 높이와 자동, 지정 여부 출력.
- 쓴 글자가 폴백 없이 채워지는 높이보다 작으면 읽기 어려움 경고. 생성은 계속함.
    - 줄 나누기: 문구를 인자로 분리.
    - `--margin`, `--border-inset`, `--flower-inset` 줄이기.
    - `--char-height` 검토.
- 폴백으로 그린 글자의 권장 높이를 계산 못 하면 경고. `--preview`로 확인.
- 하한: 선 폭 × `MIN_CHAR_HEIGHT_PER_LINE_WIDTH`.
- 글자 간격은 폰트 advance, 줄 간격은 em × `FONT_LINE_PITCH_EM`.
- 범위 검사는 실제 경로 좌표로 수행. 테두리, 꽃 안쪽까지만 허용.
- 선 폭, 선 높이, 깍지 간격: `vars` `variable_line_width`, `variable_line_height`, `variable_gap`.
    - 같은 em 높이에서 선 폭이 클수록 가는 획을 못 그림.

## 폴백 사다리

획이 선 폭보다 가늘어 첫 선이 윤곽을 충분히 덮지 못할 때 차례로 시도.

- 충분히 덮음: 못 덮은 총면적이 원 영역의 10% 이하. 조각마다 선 폭² 이하이면서 8% 이하.
    - 획이 통째로 빠지거나 끊기지 않아야 함. 기준 상수는 `@tools/fontglyphs.py`.

| 단계 | 방법           | 조건                          |
|----|--------------|-----------------------------|
| 1  | fill/outline | 첫 선이 윤곽을 충분히 덮음             |
| 2  | embolden     | 윤곽을 조금 불려 충분히 덮음. 구멍 유지     |
| 3  | center       | 못 덮은 조각에 중심선 추가로 충분히 덮음     |
| 4  | 오류           | 모두 실패. `--allow-thin`이면 건너뜀 |

- `--fallback`: `auto`(기본, 2 → 3), `none`(1만), `embolden`, `center`(그 단계만).
- 4단계 오류는 글자마다 폴백 없이 채워지는 최소 em 높이를 표시.
    - 케이크 안에 들어가면 `--char-height` 값을 제안. 자동 확대는 안 함.
    - 그 외 대응: 굵은 `--weight`, 좁은 `--line-width`.

## 한계

- 짜는 순서는 줄, 글자 순. 글자 안은 폰트 도구가 낸 순서(바깥 → 안쪽). 이동 최적화 없음.
- 이름으로는 google/fonts 패밀리만 받음. 그 외 폰트는 파일 경로로 지정.
- gcode 머리 주석의 비ASCII 문구는 `\uXXXX`로 씀. Klipper, Mainsail 호환 목적.
