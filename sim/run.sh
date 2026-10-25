#!/usr/bin/env bash
# Klipper 배치 모드 모의 실행. 실기 전송 없음.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: KLIPPER_DIR=<klipper 경로> sim/run.sh <gcode> [profile_dir]
  profile_dir   기본 hardware/active. printer.cfg, NOTES.md 사용.
환경 변수:
  KLIPPER_DIR   Klipper 소스 경로(필수). klippy/klippy.py 포함.
  KLIPPER_DICT  mcu dict 경로. 기본 $KLIPPER_DIR/out/klipper.dict.
  PYTHON        klippy 실행 파이썬. 기본 python3.
배치 모드는 PAUSE에서 멈추지 않으므로 T<n> 뒤에 RESUME을 넣어 실행함(장전 포함).
출력: out/sim.gcode, out/sim.serial, out/sim.log
종료 코드: 0 성공, 1 모의 실행 오류, 2 인자·환경 오류.
EOF
}

die() {
  printf '오류: %s\n' "$1" >&2
  exit 2
}

if [[ $# -lt 1 || $# -gt 2 || "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  [[ $# -ge 1 && ( "$1" == "-h" || "$1" == "--help" ) ]] && exit 0
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gcode="$1"
profile="${2:-$root/hardware/active}"

[[ -n "${KLIPPER_DIR:-}" ]] || die "KLIPPER_DIR 미설정. 예: KLIPPER_DIR=~/klipper sim/run.sh <gcode>"
[[ -d "$KLIPPER_DIR" ]] || die "KLIPPER_DIR 경로 없음: $KLIPPER_DIR"
klippy="$KLIPPER_DIR/klippy/klippy.py"
[[ -f "$klippy" ]] || die "klippy.py 없음: $klippy"
[[ -f "$gcode" && -r "$gcode" ]] || die "gcode 없음: $gcode"
[[ -d "$profile" ]] || die "프로필 없음: $profile"
profile="$(cd "$profile" && pwd)"
cfg="$profile/printer.cfg"
[[ -f "$cfg" ]] || die "printer.cfg 없음: $cfg"

dict="${KLIPPER_DICT:-$KLIPPER_DIR/out/klipper.dict}"
[[ -f "$dict" ]] || die "mcu dict 없음: $dict. 펌웨어 빌드로 생성함(절차: $profile/NOTES.md)"

python="${PYTHON:-python3}"
command -v "$python" >/dev/null || die "파이썬 없음: $python"

out="$root/out"
mkdir -p "$out"
job="$out/sim.gcode"
serial="$out/sim.serial"
log="$out/sim.log"
[[ "$(cd "$(dirname "$gcode")" && pwd)/$(basename "$gcode")" != "$job" ]] || die "입력은 $job 외 경로로 지정"

awk '{ print } /^T[0-9]+[[:space:]]*$/ { print "RESUME" }' "$gcode" >"$job"

status=0
"$python" "$klippy" "$cfg" -i "$job" -o "$serial" -d "$dict" >"$log" 2>&1 || status=$?

pattern='^!! |Error|Move out of range|Unknown command'
if [[ $status -ne 0 ]] || grep -qiE "$pattern" "$log"; then
  printf '실패(klippy 종료 코드 %s). 로그: %s\n' "$status" "$log" >&2
  grep -niE "$pattern" "$log" | head -n 5 >&2 || true
  printf -- '--- 마지막 20줄 ---\n' >&2
  tail -n 20 "$log" >&2
  exit 1
fi

printf '성공. 로그: %s\n' "$log"

# 마지막 print time 우선, 없으면 Finished 줄의 숫자 사용.
est="$(grep -oiE 'print[ _]time[^0-9]*[0-9]+(\.[0-9]+)?' "$log" | tail -n 1 | grep -oE '[0-9]+(\.[0-9]+)?$' || true)"
if [[ -z "$est" ]]; then
  est="$(grep -E 'Finished' "$log" | tail -n 1 | grep -oE '[0-9]+(\.[0-9]+)?' | tail -n 1 || true)"
fi
if [[ -z "$est" ]]; then
  printf '예상 시간: 파싱 불가\n'
  exit 0
fi
printf '예상 시간: %s초\n' "$est"

notes="$profile/NOTES.md"
# 제목 다음 줄에 값이 있음.
base="$(sed -n '/기준 시간/,$p' "$notes" 2>/dev/null |
  grep -oE '[0-9]+(\.[0-9]+)?[[:space:]]*초' | head -n 1 |
  grep -oE '[0-9]+(\.[0-9]+)?' || true)"
if [[ -z "$base" ]]; then
  printf '기준 시간: %s에서 찾지 못함\n' "$notes"
  exit 0
fi
awk -v e="$est" -v b="$base" 'BEGIN {
  d = e - b
  printf "기준 시간(3호 케이크): %s초, 차이 %+.1f초 (%+.1f %%)\n", b, d, (b > 0 ? d / b * 100 : 0)
}'
