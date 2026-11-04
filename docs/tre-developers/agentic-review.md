# Agentic pull request reviews

The **Agentic review with Codex** workflow reviews one pull request in the current repository.
It is a [GitHub Agentic Workflows](https://github.github.com/gh-aw/) (gh-aw) workflow that uses the Codex engine with OpenAI inference.
The workflow posts one review with inline comments on the pull request.
The review uses the `COMMENT` event. It cannot approve a pull request or request changes.

The agent job has only read permissions.
gh-aw posts the review from a separate job after its threat detection job passes.
The agent does not run pull request code, tests or deployments.

## Settings

| Setting | Location | Default or requirement |
| --- | --- | --- |
| `OPENAI_API_KEY` | `CICD` environment secret | Required |
| `CODEX_MODEL` | `CICD` environment variable or repository variable | `gpt-6-luna` |
| `CODEX_REASONING_EFFORT` | `CICD` environment variable or repository variable | `max` |

To add the key, run this command and enter the key at the prompt:

```bash
gh secret set OPENAI_API_KEY --repo OWNER/REPOSITORY --env CICD
```

Do not put the API key in an Actions variable, a workflow file, a pull request or a chat message.
gh-aw keeps the key in its API proxy container. The agent sandbox gets only a placeholder value.

The account and its billing must permit the selected model.
If you change the model, make sure that the model supports the reasoning effort.

## Run a review

1. Open **Actions > Agentic review with Codex > Run workflow**.
2. Enter the number of the pull request.
3. Start the workflow.

The review appears on the pull request when the run is complete.
The run summary shows the agent log and the token use.

Only users with write access can start the workflow.
The workflow branch that you select gives the review instructions. Select only reviewed workflow code.

## Update the workflow

`.github/workflows/agentic-review-codex.md` is the workflow source.
`.github/workflows/agentic-review-codex.lock.yml` is generated. Do not edit it.
After you change the source, compile it with [gh-aw v1.0.1](https://github.com/github/gh-aw/releases/tag/v1.0.1) and commit both files:

```bash
gh aw compile agentic-review-codex
```

The activation job stops the run if the lock file does not match the source.
