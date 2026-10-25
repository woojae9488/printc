#!/usr/bin/env bash
# Klipper 없는 호스트에서 Docker로 펌웨어 dict 빌드 후 sim/run.sh 실행. 실기 전송 없음.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: sim/docker.sh <gcode> [profile_dir]
  gcode, profile_dir   저장소 안 경로. 규약은 sim/run.sh -h.
환경 변수:
  KLIPPER_REF   Klipper 태그, 브랜치, 커밋. 기본 sim/Dockerfile의 ARG 값.
동작: 이미지 없으면 빌드(태그는 ref, Dockerfile 해시).
  프로필 firmware.config로 klipper.dict 빌드(볼륨 캐시, 키는 firmware.config, 이미지 ID). 이어서 sim/run.sh 실행.
종료 코드: 0 성공, 1 모의 실행 오류, 2 인자·환경 오류(이미지, dict 빌드 실패 포함).
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

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
dockerfile="$root/sim/Dockerfile"
volume="printc-klipper-fw"

command -v docker >/dev/null || die "docker 없음"
docker info >/dev/null 2>&1 || die "Docker 데몬 응답 없음. Docker Desktop 실행 필요"

# 저장소 안 경로를 /work 기준 상대 경로로 변환.
rel() {
  case "$1" in
    "$root"/*) printf '%s' "${1#"$root"/}" ;;
    *) die "저장소 밖 경로: $1" ;;
  esac
}

[[ -f "$1" && -r "$1" ]] || die "gcode 없음: $1"
gcode="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
gcode_rel="$(rel "$gcode")"
profile="${2:-$root/hardware/active}"
[[ -d "$profile" ]] || die "프로필 없음: $profile"
profile="$(cd "$profile" && pwd -P)"
profile_rel="$(rel "$profile")"
fwcfg="$profile/firmware.config"
[[ -f "$fwcfg" ]] || die "firmware.config 없음: $fwcfg"
[[ ! -L "$fwcfg" ]] || die "firmware.config 심볼릭 링크 금지: $fwcfg"
# --mount 값은 쉼표로 구분됨.
[[ "$root$fwcfg" != *,* ]] || die "경로에 쉼표 금지: $root"

ref="${KLIPPER_REF:-$(sed -n 's/^ARG KLIPPER_REF=//p' "$dockerfile")}"
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$ ]] || die "KLIPPER_REF 형식 오류: '$ref'"

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum; else shasum -a 256; fi
}
# Dockerfile이 바뀌면 태그가 달라져 재빌드됨.
image="printc-klipper:${ref//\//-}-$(sha256 <"$dockerfile" | cut -c1-12)"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  printf '이미지 빌드: %s\n' "$image" >&2
  docker build --build-arg KLIPPER_REF="$ref" -t "$image" - <"$dockerfile" >&2 || die "이미지 빌드 실패: $image"
fi
image_id="$(docker image inspect -f '{{.Id}}' "$image")" || die "이미지 ID 조회 실패: $image"
key="$({ cat "$fwcfg"; printf '%s\n' "$image_id"; } | sha256 | cut -c1-16)"

isolate=(--rm --network none --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp)
cache="type=volume,src=$volume,dst=/cache"

# 볼륨 쓰기만 root로 수행. 저장소는 마운트하지 않음.
# 실행 사용자(호스트 UID)가 읽도록 권한을 열고 완성 후 이름을 바꿔 중단 시 반쪽 캐시 방지.
# 동시 실행으로 이미 캐시가 생겼으면 mv가 실패하고 임시 디렉터리는 버림.
build="$(cat <<'EOF'
set -euo pipefail
dir="/cache/$1"
if [[ -f "$dir/out/klipper.dict" ]]; then
  printf '펌웨어 dict 캐시 사용: %s\n' "$1"
  exit 0
fi
tmp="$(mktemp -d /cache/.build.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
cp /fw/firmware.config "$tmp/.config"
make KCONFIG_CONFIG="$tmp/.config" OUT="$tmp/out/" olddefconfig
make KCONFIG_CONFIG="$tmp/.config" OUT="$tmp/out/" -j"$(nproc)"
chmod -R a+rX "$tmp"
mv -T "$tmp" "$dir" 2>/dev/null || [[ -f "$dir/out/klipper.dict" ]]
EOF
)"
docker run "${isolate[@]}" --mount "$cache" \
  --mount "type=bind,src=$fwcfg,dst=/fw/firmware.config,readonly" \
  "$image" bash -c "$build" build "$key" >&2 || die "펌웨어 dict 빌드 실패"

mkdir -p "$root/out"
status=0
docker run "${isolate[@]}" --mount "$cache,readonly" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  --mount "type=bind,src=$root,dst=/work,readonly" \
  --mount "type=bind,src=$root/out,dst=/work/out" \
  -e KLIPPER_DIR=/opt/klipper -e PYTHON=/opt/venv/bin/python \
  -e KLIPPER_DICT="/cache/$key/out/klipper.dict" \
  -w /work "$image" sim/run.sh "$gcode_rel" "$profile_rel" || status=$?
# docker 자체 오류(125 등)는 환경 오류로 봄.
[[ $status -le 2 ]] || status=2
exit "$status"
