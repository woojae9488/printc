---
name: skill-guide
description: 스킬, 규칙, CLAUDE.md를 새로 만들거나 수정하거나 점검할 때 컨벤션 적용. 스킬 추가, 삭제, 리뷰 시 사용.
paths:
  - .claude/**
  - CLAUDE.md
---

# skill-guide

컨벤션은 `@.claude/rules/`를 따름.

## 생성

1. 기본 스킬로 대체 가능한지 확인. 가능하면 중단하고 보고.
2. 요청 범위 확정. 불명확하면 질문.
3. `@.claude/skills/<name>/SKILL.md` 작성.

## 점검

1. 각 규칙 위반 여부 확인(frontmatter, 줄 수, 어투, 중복, 기계 값).
2. 위반 항목 보고. 반영은 `@.claude/rules/review.md`를 따름.

## 규칙 추가

- 항상 적용: `@.claude/rules/<주제>.md`.
- 특정 경로만: frontmatter `paths`에 글롭 지정.
