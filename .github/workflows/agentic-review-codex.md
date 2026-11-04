---
name: Agentic review with Codex
description: Review a pull request with Codex and post one review with inline comments.
on:
  workflow_dispatch:
    inputs:
      pull_request_number:
        description: Number of the pull request to review
        type: string
        required: true
      reasoning_effort:
        description: Reasoning effort of the model
        type: choice
        options: [low, medium, high, xhigh, max]
        default: xhigh
environment: CICD
permissions:
  contents: read
  pull-requests: read
timeout-minutes: 30
max-turns: 60
concurrency:
  job-discriminator: ${{ inputs.pull_request_number }}
imports:
  - shared/pr-review.md
engine:
  id: codex
  model: ${{ vars.CODEX_MODEL || 'gpt-6-luna' }}
  env:
    CODEX_REASONING_EFFORT: ${{ inputs.reasoning_effort }}
  config: |
    model_reasoning_effort = "${CODEX_REASONING_EFFORT}"
---

<!-- markdownlint-disable-file MD041 -->
Follow the review task.
