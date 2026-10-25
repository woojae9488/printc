# deploy/*.sh 공용 함수. source 전용. macOS 기본 bash 3.2 호환.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)
moonraker_timeout=5 # 초. Moonraker API 요청당 최대 시간.

# 원격 스크립트 앞부분. $1~$3은 PI_REPO, PI_CONFIG, PI_KLIPPER. 비면 원격 $HOME 기준 기본값.
remote_env='set -euo pipefail
repo="${1:-$HOME/printc}"
config="${2:-$HOME/printer_data/config}"
klipper="${3:-$HOME/klipper}"
shift 3
err() { printf "오류: %s\n" "$1" >&2; exit 1; }
# 사용: moonraker <API 경로> [curl 옵션...].
moonraker() {
  local api="$1"
  shift
  curl -fsS --max-time '"$moonraker_timeout"' "$@" "http://localhost:7125$api"
}
# 인쇄 중(printing, paused)이거나 상태 조회 실패면 중단. 사용: need_idle [안내].
need_idle() {
  local st state
  st="$(moonraker "/printer/objects/query?print_stats=state")" ||
    err "Moonraker 인쇄 상태 조회 실패${1:+. $1}"
  state="$(printf "%s" "$st" | tr -d "[:space:]" | grep -Eo "\"state\":\"[a-z_]+\"" | head -n 1 || true)"
  case "$state" in
    *standby* | *complete* | *cancelled* | *error*) ;;
    *printing* | *paused*) err "인쇄 중. 끝난 뒤 실행${1:+. $1}" ;;
    *) err "인쇄 상태 응답 해석 실패${1:+. $1}" ;;
  esac
}
'

die() {
  printf '오류: %s\n' "$1" >&2
  exit 2
}

need_host() {
  [[ -n "${PI_HOST:-}" ]] || die "PI_HOST 미설정. 예: PI_HOST=pi@printc.local $0"
  [[ "$PI_HOST" != -* && "$PI_HOST" != *[[:space:]]* ]] || die "PI_HOST 형식 오류: '$PI_HOST'"
  command -v ssh >/dev/null || die "ssh 없음"
  local v
  for v in PI_REPO PI_CONFIG PI_KLIPPER; do
    [[ -z "${!v:-}" || "${!v}" == /* ]] || die "${v}는 Pi 기준 절대 경로로 지정: '${!v}'"
  done
}

# POSIX 계열 셸 기준 작은따옴표 인용. bash 3.2는 치환 문자열의 \를 남기므로 변수 사용.
sq() {
  local s="'" q="'\\''"
  printf "'%s'" "${1//$s/$q}"
}

# 사용: run_remote <script> [인자...]. stdin은 원격 스크립트로 전달됨.
run_remote() {
  local cmd status=0
  cmd="bash -c $(sq "$remote_env$1") printc $(sq "${PI_REPO:-}") $(sq "${PI_CONFIG:-}") $(sq "${PI_KLIPPER:-}")"
  shift
  for a in "$@"; do cmd+=" $(sq "$a")"; done
  ssh "${ssh_opts[@]}" -- "$PI_HOST" "$cmd" || status=$?
  case "$status" in
    0) return 0 ;;
    255) die "ssh 접속 실패: $PI_HOST" ;;
    *)
      printf '오류: 원격 작업 실패(종료 코드 %s)\n' "$status" >&2
      exit 1
      ;;
  esac
}

# -n 출력. 사용: show_remote <script> [이름=값...].
show_remote() {
  printf '원격 명령: ssh %s %s, bash -c 아래 스크립트\n' "${ssh_opts[*]}" "$PI_HOST"
  printf '  PI_REPO=%s PI_CONFIG=%s PI_KLIPPER=%s\n' "${PI_REPO:-\$HOME/printc}" \
    "${PI_CONFIG:-\$HOME/printer_data/config}" "${PI_KLIPPER:-\$HOME/klipper}"
  local script="$1"
  shift
  [[ $# -eq 0 ]] || printf '  %s\n' "$*"
  printf '%s\n' "$script" | sed 's/^/  | /'
}
