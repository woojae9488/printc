---
name: skill-guide
description: 스킬, 규칙, CLAUDE.md 생성, 수정, 점검 시 컨벤션 적용. 스킬 추가, 리뷰 시 사용.
paths:
  - .claude/**
  - CLAUDE.md
---

# skill-guide

컨벤션은 `@.claude/rules/`를 따름.

## 생성

1. `@.claude/rules/skills.md`의 대체 가능 여부 기준 확인. 가능하면 중단하고 보고.
2. 요청 범위 확정. 불명확하면 질문.
3. `@.claude/skills/<name>/SKILL.md` 작성.

## 점검

1. 각 규칙 위반 여부 확인(frontmatter, 줄 수, 어투, 서식, 중복, 기계 값).
2. 위반 항목 보고. 반영은 `@.claude/rules/review.md`를 따름.

## 규칙 추가

- 항상 적용: `@.claude/rules/<주제>.md`.
- 특정 경로만: frontmatter `paths`에 글롭 지정.
