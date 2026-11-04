---
name: Agentic review with Copilot
environment: ${{ inputs.environment }}
timeout-minutes: 30
on:
  reaction: none
  status-comment: false
  workflow_call:
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
  copilot-requests: write
env:
  REVIEW_HARNESS: copilot
engine:
  id: copilot
  model: ${{ vars.COPILOT_MODEL }}
  env:
    REVIEW_COPILOT_REASONING_EFFORT: ${{ vars.COPILOT_REASONING_EFFORT }}
  harness:
    use: review_copilot_harness.cjs
---

<review_instructions>
<!-- markdownlint-disable-file MD041 -->
{{#runtime-import .github/aw/branch-review.md}}
</review_instructions>
