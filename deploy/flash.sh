#!/usr/bin/env bash
# Pi에서 프로필 firmware.config로 Klipper 펌웨어를 빌드해 MCU에 올림.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: PI_HOST=<user@host> deploy/flash.sh [profile_dir] [-n] [--yes]
  profile_dir   기본 hardware/active. firmware.config, printer.cfg([mcu] serial) 사용.
                printer.cfg 자체의 [mcu]만 읽음. include는 따라가지 않음.
  -n            요약, 원격 명령만 출력.
  --yes         실행 확인 생략. tty 없으면 필수.
환경 변수:
  PI_HOST      ssh 대상(필수).
  PI_KLIPPER   Pi Klipper 소스 경로. 기본 $HOME/klipper(원격 기준). Pi 기준 절대 경로. 아니면 종료 2.
동작: Pi Klipper 체크아웃이 sim/Dockerfile의 KLIPPER_REF와 같은지 확인. 다르면 중단.
  시리얼 장치, sudo, 인쇄 상태(Moonraker) 확인. 인쇄 중이거나 조회 실패면 중단.
  .config 전송, make olddefconfig, make clean, make.
  klipper 중지, make flash, klipper 시작(실패해도 시작).
종료 코드: 0 성공, 1 원격 작업 실패(인쇄 중, 조회 실패 포함), 2 인자·환경 오류(ssh 접속 실패, 확인 거부 포함).
EOF
}

# shellcheck source=deploy/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

dry=0
yes=0
profile=""
for a in "$@"; do
  case "$a" in
    -h | --help) usage; exit 0 ;;
    -n) dry=1 ;;
    --yes) yes=1 ;;
    -*) usage >&2; exit 2 ;;
    *)
      [[ -z "$profile" ]] || { usage >&2; exit 2; }
      profile="$a"
      ;;
  esac
done

need_host
profile="${profile:-$root/hardware/active}"
[[ -d "$profile" ]] || die "프로필 없음: $profile"
profile="$(cd "$profile" && pwd -P)"
fwcfg="$profile/firmware.config"
cfg="$profile/printer.cfg"
[[ -f "$fwcfg" ]] || die "firmware.config 없음: $fwcfg"
[[ -f "$cfg" ]] || die "printer.cfg 없음: $cfg"

ref="$(sed -n 's/^ARG KLIPPER_REF=//p' "$root/sim/Dockerfile")"
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$ ]] || die "sim/Dockerfile KLIPPER_REF 형식 오류: '$ref'"

# printer.cfg 자체 [mcu] 섹션의 serial. include는 따라가지 않음. 줄 끝 주석 제거.
serial="$(awk '
  /^[[:space:]]*\[/ { s = ($0 ~ /^[[:space:]]*\[mcu\][[:space:]]*([#;].*)?$/) }
  s && /^[[:space:]]*serial[[:space:]]*[:=]/ {
    sub(/^[^:=]*[:=][[:space:]]*/, ""); sub(/[[:space:]]*[#;].*$/, ""); sub(/[[:space:]]+$/, ""); print; exit
  }' "$cfg")"
[[ -n "$serial" ]] || die "[mcu] serial 없음: $cfg"
[[ "$serial" =~ ^/dev/[A-Za-z0-9._:/+-]+$ ]] || die "[mcu] serial 형식 오류: '$serial'"

remote='ref="$1"
serial="$2"
fw="$(cat)"
cd -- "$klipper" 2>/dev/null || err "Klipper 없음: $klipper"
want="$(git rev-parse -q --verify "$ref^{commit}")" || err "Pi Klipper에 $ref 없음. git fetch --tags 후 checkout 필요"
[[ "$(git rev-parse HEAD)" == "$want" ]] ||
  err "Klipper 버전 불일치: Pi $(git describe --tags --always), 기준 $ref. git -C $klipper checkout $ref 필요"
[[ -e "$serial" ]] || err "시리얼 장치 없음: $serial"
sudo -n true 2>/dev/null || err "sudo 비밀번호 없이 실행 불가"
need_idle
printf "%s\n" "$fw" >.config
make olddefconfig
make clean
make
trap "sudo -n service klipper start || { printf \"오류: klipper 시작 실패\\n\" >&2; exit 1; }" EXIT
trap "exit 1" HUP INT TERM
sudo -n service klipper stop
make flash FLASH_DEVICE="$serial"
printf "펌웨어 업로드 완료\n"'

printf '대상: %s\n' "$PI_HOST"
printf 'Klipper ref: %s (sim/Dockerfile)\n' "$ref"
printf '시리얼: %s\n' "$serial"
printf '프로필: %s\n' "$profile"
if [[ $dry -eq 1 ]]; then
  show_remote "$remote" "\$1(ref)=$ref" "\$2(serial)=$serial" "stdin=$fwcfg"
  exit 0
fi

if [[ $yes -ne 1 ]]; then
  [[ -t 0 ]] || die "확인 입력 불가. --yes 필요"
  read -r -p '펌웨어 업로드 진행? [y/N] ' ans || ans=""
  [[ "$ans" == [yY] ]] || die "취소함"
fi

run_remote "$remote" "$ref" "$serial" <"$fwcfg"
