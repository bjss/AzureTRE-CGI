---
name: Agentic review with Copilot CLI
description: Review a pull request with Copilot CLI and an OpenAI key, and post one review with inline comments.
on:
  workflow_dispatch:
    inputs:
      pull_request_number:
        description: Number of the pull request to review
        type: string
        required: true
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
  id: copilot
  model: ${{ vars.COPILOT_BYOK_MODEL || 'gpt-6-luna' }}
  args: ["--reasoning-effort", "medium"]
  env:
    COPILOT_PROVIDER_BASE_URL: https://api.openai.com/v1
    COPILOT_PROVIDER_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    COPILOT_PROVIDER_WIRE_API: responses
    COPILOT_PROVIDER_MAX_PROMPT_TOKENS: "60000"
---

<!-- markdownlint-disable-file MD041 -->
Follow the review task.
