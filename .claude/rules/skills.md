---
paths:
  - .claude/skills/**
---

# 스킬 컨벤션

- 새 스킬 전에 기본 스킬로 대체 가능한지 확인. 가능하면 만들지 않음.
- 경로: `@.claude/skills/<name>/SKILL.md`. name은 디렉터리명과 동일.
- name 형식: `<대상>-<동작>`. 영문 소문자 2단어, 하이픈 구분(예: `klipper-sim`, `git-commit`).
- 기본 스킬과 이름 중복 금지.
- frontmatter는 `name`, `description`, `paths`만 사용.
- 특정 경로 작업에만 쓰는 스킬은 `paths`로 글롭 제한.
- description은 1문장. 무엇을 하는지와 호출 시점 포함.
- 본문 40줄 이내. 번호 절차 위주.
- 규칙(`@.claude/rules/`)에 있는 내용은 본문에 반복하지 않음.
