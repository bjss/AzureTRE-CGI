---
description: Review an immutable branch comparison and save a report without modifying GitHub or Azure resources.
permissions:
  contents: read
  pull-requests: read
max-turns: 60
max-ai-credits: 500
network:
  allowed:
    - defaults
tools:
  edit: false
  bash: true
  github:
    toolsets: [repos, pull_requests]
pre-agent-steps:
  - name: Prepare immutable review inputs
    uses: actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd # v8.0.0
    env:
      REVIEW_PR_NUMBER: ${{ inputs.pull_request_number }}
      REVIEW_TARGET_REF: ${{ inputs.target_ref }}
      REVIEW_BASE_REF: ${{ inputs.base_ref }}
      CODEX_MODEL: ${{ vars.CODEX_MODEL || 'gpt-6-luna' }}
      CODEX_REASONING_EFFORT: ${{ vars.CODEX_REASONING_EFFORT || 'max' }}
      COPILOT_REASONING_EFFORT: ${{ vars.COPILOT_REASONING_EFFORT }}
    with:
      script: |
        const { prepareReview } = require('./.github/scripts/agentic-review.js');
        await prepareReview({ github, context, env: process.env });
  - name: Stage trusted review helpers outside the writable checkout
    run: |
      cp .github/scripts/agentic-review.js .github/scripts/agentic-review-copilot.cjs "$RUNNER_TEMP/gh-aw/actions/"
      cp .github/scripts/agentic-review-copilot.cjs "$RUNNER_TEMP/gh-aw/actions/review_copilot_harness.cjs"
post-steps:
  - name: Collect the review report
    uses: actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd # v8.0.0
    with:
      script: |
        const path = require('node:path');
        const { collectReport } = require(path.join(process.env.RUNNER_TEMP, 'gh-aw/actions/agentic-review.js'));
        collectReport(process.env);
  - name: Upload the review report
    uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
    with:
      name: agentic-review-report
      path: ${{ runner.temp }}/agentic-review-report/
      if-no-files-found: error
      retention-days: 7
---
