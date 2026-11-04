# Agentic pull request reviews

Two [GitHub Agentic Workflows](https://github.github.com/gh-aw/) (gh-aw) workflows review one pull request in the current repository:

- **Agentic review with Codex** uses Codex CLI with OpenAI inference.
- **Agentic review with Copilot CLI** uses Copilot CLI in bring-your-own-key (BYOK) mode with the same OpenAI key. The workflow does not request a Copilot permission or a Copilot token.

Each workflow posts one review with inline comments on the pull request.
The review uses the `COMMENT` event. It cannot approve a pull request or request changes.

The agent job has only read permissions.
gh-aw posts the review from a separate job after its threat detection job passes.
The agent does not run pull request code, tests or deployments.

## Settings

| Setting | Location | Default or requirement |
| --- | --- | --- |
| `OPENAI_API_KEY` | `CICD` environment secret | Required |
| `CODEX_MODEL` | `CICD` environment variable or repository variable | `gpt-6-luna` |
| `COPILOT_BYOK_MODEL` | `CICD` environment variable or repository variable | `gpt-6-luna` |

To add the key, run this command and enter the key at the prompt:

```bash
gh secret set OPENAI_API_KEY --repo OWNER/REPOSITORY --env CICD
```

Do not put the API key in an Actions variable, a workflow file, a pull request or a chat message.
gh-aw keeps the key in its API proxy container. The agent sandbox gets only a placeholder value.

The account and its billing must permit the selected model.
Both workflows use the same OpenAI organisation, so they share its token-per-minute limit.
Neither CLI can limit its own tokens per minute.
If OpenAI refuses a request for a rate limit, the run can wait until it stops at its 30-minute limit.
If you change the model, make sure that the model supports the reasoning effort.

## Run a review

1. Open **Actions**, and then select **Agentic review with Codex** or **Agentic review with Copilot CLI**.
2. Select **Run workflow**, and then enter the number of the pull request.
3. For Codex, select the reasoning effort. The default is `xhigh`.
4. Start the workflow.

Copilot CLI always uses the `medium` reasoning effort.
gh-aw refuses a workflow input in the Copilot CLI arguments, so the effort is a fixed value.

The review appears on the pull request when the run is complete.
The run summary shows the agent log and the token use.

Only users with write access can start the workflow.
The workflow branch that you select gives the review instructions. Select only reviewed workflow code.

## Update the workflow

The sources are `.github/workflows/agentic-review-codex.md` and `.github/workflows/agentic-review-copilot.md`.
They import the prompt and the safe outputs from `.github/workflows/shared/pr-review.md`.
The `.lock.yml` files are generated. Do not edit them.
After you change a source, compile it with [gh-aw v1.0.1](https://github.com/github/gh-aw/releases/tag/v1.0.1) and commit the changed files:

```bash
gh aw compile agentic-review-codex agentic-review-copilot
```

The activation job stops the run if the lock file does not match the source.
