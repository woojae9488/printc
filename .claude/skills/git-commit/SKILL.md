---
name: git-commit
description: 변경 사항을 컨벤션에 맞게 커밋. 커밋 요청 시 사용.
---

# git-commit

사용자가 요청할 때만 커밋.

1. `git status`, `git diff`로 변경 확인.
2. 기본 브랜치면 작업 브랜치 생성. 이름은 `<type>/<요약>`.
3. cfg, 매크로, 도구 변경이 있으면 `/klipper-sim` 실행. 실패 시 중단.
4. 비밀 값, 생성물 포함 여부 확인(`@.gitignore`). 있으면 제외.
5. 목적이 다른 변경은 커밋 분리. 파일은 이름으로 지정해 스테이징.
6. 메시지 작성: `<type>: <요약>`. 금지 사항은 `@.claude/rules/git.md`.
    - type: `feat`, `fix`, `docs`, `refactor`, `chore`, `hw`(프로필, 보정값)
    - 요약은 50자 이내, 단순 종결형, 마침표 없음.
    - 이유가 필요하면 본문에 1~2줄.
7. 커밋 후 `git log -1`로 확인.

`--no-verify`, `--amend`, 강제 푸시 금지.
