---
name: Agentic review with Codex
environment: ${{ inputs.environment }}
timeout-minutes: 30
on:
  reaction: none
  status-comment: false
  workflow_call:
    secrets:
      OPENAI_API_KEY:
        required: true
    inputs:
      pull_request_number:
        type: string
        default: ''
      target_ref:
        type: string
        default: ''
      base_ref:
        type: string
        default: main
      environment:
        type: string
        default: CICD
safe-outputs:
  create-issue: false
  report-failure-as-issue: false
  report-failed-jobs: false
  staged: true
  noop:
    report-as-issue: false
  report-missing-data: false
  report-missing-tool: false
  report-incomplete: false
imports:
  - shared/agentic-review.md
permissions:
  contents: read
  pull-requests: read
env:
  REVIEW_HARNESS: codex
engine:
  id: codex
  model: ${{ vars.CODEX_MODEL || 'gpt-6-luna' }}
  env:
    CODEX_REASONING_EFFORT: ${{ vars.CODEX_REASONING_EFFORT || 'max' }}
  config: |
    model_reasoning_effort = "${CODEX_REASONING_EFFORT}"
---

<review_instructions>
<!-- markdownlint-disable-file MD041 -->
{{#runtime-import .github/aw/branch-review.md}}
</review_instructions>
