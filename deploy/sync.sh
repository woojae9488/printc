#!/usr/bin/env bash
# 저장소 추적 파일을 Pi PI_REPO로 복사하고 Klipper 재시작. 삭제는 반영하지 않음.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: PI_HOST=<user@host> deploy/sync.sh [-n] [--init] [--no-restart]
  -n            전송 없이 대상 파일 요약, 원격 명령 출력.
  --init        최초 1회. PI_CONFIG/printc를 PI_REPO로 링크.
                PI_CONFIG/printer.cfg, moonraker.conf 각각 없을 때만 include 한 줄로 생성.
                있으면 그대로 두고 교체할 한 줄을 안내.
  --no-restart  Klipper 재시작 생략.
환경 변수:
  PI_HOST     ssh 대상(필수).
  PI_REPO     Pi 저장소 경로. 기본 $HOME/printc(원격 기준).
  PI_CONFIG   Pi Klipper 설정 디렉터리. 기본 $HOME/printer_data/config.
  경로 변수는 Pi 기준 절대 경로. 아니면 종료 2.
동작: git ls-files 파일(미커밋 변경 포함)을 tar로 묶어 ssh로 풀어 넣음. 심볼릭 링크 보존.
  로컬 임시 파일에 다 묶은 뒤 전송. 재시작은 Moonraker API 사용.
  인쇄 중이거나 인쇄 상태 조회 실패(401, 503, 시간 초과 등)면 아무것도 바꾸지 않고 중단.
  --no-restart 시 인쇄 상태 확인 생략.
종료 코드: 0 성공, 1 원격 작업 실패(인쇄 중, 조회 실패 포함),
  2 인자·환경 오류(ssh 접속 실패, 로컬 tar 실패 포함).
EOF
}

# shellcheck source=deploy/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

dry=0
init=0
restart=1
for a in "$@"; do
  case "$a" in
    -h | --help) usage; exit 0 ;;
    -n) dry=1 ;;
    --init) init=1 ;;
    --no-restart) restart=0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

need_host
command -v git >/dev/null || die "git 없음"
command -v tar >/dev/null || die "tar 없음"
git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || die "git 저장소 아님: $root"

# 작업 트리에서 지운 추적 파일은 제외.
files=()
missing=0
while IFS= read -r -d '' f; do
  if [[ -e "$root/$f" || -L "$root/$f" ]]; then files+=("$f"); else missing=$((missing + 1)); fi
done < <(git -C "$root" ls-files -z)
[[ ${#files[@]} -gt 0 ]] || die "추적 파일 없음: $root"

remote='[[ "$2" != 1 ]] || need_idle "확인 없이 진행하려면 --no-restart 사용"
mkdir -p -- "$repo"
repo="$(cd -- "$repo" && pwd)"
tar -xf - -C "$repo"
printf "동기화 완료: %s\n" "$repo"
if [[ "$1" == 1 ]]; then
  [[ -d "$config" ]] || err "설정 디렉터리 없음: $config"
  link="$config/printc"
  [[ -L "$link" || ! -e "$link" ]] || err "링크 자리에 파일 있음: $link"
  ln -sfn -- "$repo" "$link"
  printf "링크: %s -> %s\n" "$link" "$repo"
  # 사용: ensure_include <파일 이름> <include 한 줄>.
  ensure_include() {
    local path="$config/$1"
    if [[ ! -e "$path" && ! -L "$path" ]]; then
      printf "%s\n" "$2" >"$path"
      printf "%s 생성: %s\n" "$1" "$path"
    elif grep -qxF -- "$2" "$path"; then
      printf "%s 유지(include 있음): %s\n" "$1" "$path"
    else
      printf "%s 있음. 덮어쓰지 않음. 내용을 다음 한 줄로 직접 교체: %s\n" "$1" "$2"
    fi
  }
  ensure_include printer.cfg "[include printc/hardware/active/printer.cfg]"
  ensure_include moonraker.conf "[include printc/klipper/moonraker.conf]"
  printf "moonraker.conf를 만들거나 바꿨다면 sudo service moonraker restart 필요\n"
fi
if [[ "$2" == 1 ]]; then
  moonraker /printer/restart -X POST >/dev/null || err "Klipper 재시작 요청 실패"
  printf "Klipper 재시작 요청함\n"
fi'

if [[ $dry -eq 1 ]]; then
  printf '대상: %s\n' "$PI_HOST"
  printf '파일: %s개' "${#files[@]}"
  [[ $missing -eq 0 ]] || printf '(작업 트리에 없는 %s개 제외)' "$missing"
  printf '\n'
  printf '%s\n' "${files[@]}" |
    awk -F/ '{ n[NF > 1 ? $1 "/" : $1]++ } END { for (k in n) printf "  %-24s %d\n", k, n[k] }' | sort
  for f in "${files[@]}"; do
    [[ ! -L "$root/$f" ]] || printf '  링크 %s -> %s\n' "$f" "$(readlink "$root/$f")"
  done
  [[ -z "$(git -C "$root" status --porcelain --untracked-files=no)" ]] || printf '미커밋 변경 포함\n'
  show_remote "$remote" "\$1(init)=$init" "\$2(restart)=$restart"
  exit 0
fi

# 묶기가 끝난 아카이브만 보냄. 중간 실패 시 원격은 바뀌지 않음.
archive="$(mktemp "${TMPDIR:-/tmp}/printc-sync.XXXXXX")" || die "임시 파일 생성 실패"
trap 'rm -f -- "$archive"' EXIT
# macOS tar의 확장 속성, ._ 파일 제외.
printf '%s\0' "${files[@]}" | COPYFILE_DISABLE=1 tar -C "$root" --no-xattrs --null -T - -cf "$archive" ||
  die "로컬 tar 실패"
run_remote "$remote" "$init" "$restart" <"$archive"
