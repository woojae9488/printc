# Klipper 모의 실행, 펌웨어 dict 빌드 이미지. 보드 설정은 실행 시 프로필에서 받음.
FROM ubuntu:22.04

ARG KLIPPER_REF=v0.13.0
ARG KLIPPER_REPO=https://github.com/Klipper3d/klipper.git
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git make gcc libc6-dev libffi-dev python3 python3-dev python3-venv \
        gcc-avr binutils-avr avr-libc \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
    && rm -rf /var/lib/apt/lists/*

# 태그, 브랜치, 커밋 모두 받도록 clone 대신 fetch 사용.
RUN git init -q /opt/klipper \
    && git -C /opt/klipper fetch -q --depth 1 -- "$KLIPPER_REPO" "$KLIPPER_REF" \
    && git -C /opt/klipper checkout -q FETCH_HEAD

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /opt/klipper/scripts/klippy-requirements.txt

# 실행 사용자는 /opt 쓰기 불가라 C 헬퍼, 바이트코드를 미리 만듦.
RUN /opt/venv/bin/python /opt/klipper/klippy/chelper/__init__.py \
    && /opt/venv/bin/python -m compileall -q /opt/klipper/klippy

WORKDIR /opt/klipper
