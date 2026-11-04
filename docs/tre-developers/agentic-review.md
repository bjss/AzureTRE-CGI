# Agentic branch reviews

The **Agentic branch review** workflow reviews an unmerged branch or pull request in the current repository.
It uses [GitHub Agentic Workflows](https://github.github.com/gh-aw/) with GitHub Copilot by default.
Set `AGENT_HARNESS` to `codex` to select the Codex harness with OpenAI inference.

The workflow records immutable commit SHAs before inference.
It reads the comparison through GitHub's API, without checking out or executing the target branch.
The report appears in the Actions summary and the `agentic-review-report` artifact.
It includes findings, validation recommendations and coverage limits.
The agent receives GitHub read permissions. It cannot approve pull requests or change repository resources.
Compiler-managed issue creation and failure reporting are disabled.

## Repository and environment settings

Set `AGENT_HARNESS` under **Settings > Secrets and variables > Actions > Variables**.
This selector is a repository variable because the router runs before selecting an environment.
The default requires no custom configuration variables.

| Setting | Location | Default or requirement |
| --- | --- | --- |
| `AGENT_HARNESS` | Repository variable | `copilot`. Accepted values: `copilot`, `codex`. |
| `CODEX_MODEL` | Selected environment or repository variable | `gpt-6-luna` |
| `CODEX_REASONING_EFFORT` | Selected environment or repository variable | `max` |
| `OPENAI_API_KEY` | Selected environment secret | Required for Codex |
| `COPILOT_MODEL` | Selected environment or repository variable | Unset, preserving Copilot's default model |
| `COPILOT_REASONING_EFFORT` | Selected environment or repository variable | Unset, preserving Copilot's default effort |

The selected environment defaults to `CICD`.
Environment variables override repository variables with the same name.
Keep model identifiers and reasoning settings in variables, and credentials in secrets.
Adding an OpenAI secret does not change the selected harness.
An invalid harness or reasoning setting fails the run instead of silently selecting another engine.

### Configure Codex

1. Open **Settings > Environments > CICD** in the target repository.
2. Under **Environment secrets**, add `OPENAI_API_KEY` with the OpenAI API key as its value.
3. Set the repository variable `AGENT_HARNESS` to `codex`.
4. If required, override `CODEX_MODEL` or `CODEX_REASONING_EFFORT` using environment or repository variables.

Do not put the API key in an Actions variable, workflow file, pull request or chat message.
A native preflight job verifies that the selected environment supplies `OPENAI_API_KEY` before starting Codex.
gh-aw excludes engine credentials from the agent sandbox and routes inference through its proxy.
The workflow references only the selected engine's credentials. It does not inherit all environment secrets.
The router explicitly passes `OPENAI_API_KEY` by name to the reusable Codex workflow.
That declaration is required even when the value exists only in the selected environment.

Inside the sandbox, AWF sets non-secret placeholder values for `OPENAI_API_KEY` and `CODEX_API_KEY`.
The proxy replaces the placeholder with the real API key, which stays in the proxy container.
Do not wrap gh-aw's Codex harness in another process.
AWF removes a key from the environment after the first read, so a wrapper that reads the key hides it from the harness.

Alternatively, run this command locally and enter the key at the secure prompt:

```bash
gh secret set OPENAI_API_KEY --repo OWNER/REPOSITORY --env CICD
```

The defaults use [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) with `max` reasoning.
Account access and billing must permit that model.
Changing models can require changing the reasoning level to one that the model supports.

### Configure Copilot

Leave `AGENT_HARNESS` unset, or set it to `copilot`.
Leave the Copilot model and effort variables unset to retain the installed CLI's defaults.
The effort adapter adds a CLI flag only when an effort override is supplied.
It delegates execution to gh-aw's built-in Copilot harness, preserving its policy, retries and timeouts.

The workflow grants `copilot-requests: write` for organisation-billed inference.
The organisation must have an eligible Copilot subscription with centralised billing enabled.
No Copilot PAT is required in this mode.
See [gh-aw authentication](https://github.github.com/gh-aw/reference/auth/) for access requirements.
Selecting Copilot ignores the Codex settings and the OpenAI key.

## Run a review

1. Open **Actions > Agentic branch review > Run workflow**.
2. Select the trusted branch containing the workflow definition.
3. Enter a pull request number, or enter a `target_ref` and its comparison `base_ref`.
4. Select the environment containing the engine's secrets. The default is `CICD`.
5. Start the workflow and inspect its summary and report artifact.

For a pull request, leave `target_ref` empty. The workflow uses that pull request's base and head commits.
For a branch, leave the pull request number empty. `base_ref` defaults to `main`.
The workflow branch selector chooses the reviewer code. `target_ref` chooses the code being reviewed.
Reviewing a branch does not require merging it.

Manual dispatch requires the router workflow to exist on the repository's default branch first.
Before that first merge, use an isolated test repository or register an inert workflow at the same router path.
Once registered, select a feature branch to exercise its router and compiled child workflows.
The child workflows use the same commit as the router.
Dispatching an untrusted workflow branch gives that workflow access to the selected environment's credentials.
Select only reviewed workflow code. Treat target-branch content as review evidence.

The workflow has no automatic PR trigger and does not post review comments.
Creating a pull request runs only the deterministic **Agentic review checks** job when its path filters match.
That check does not invoke a model or use engine secrets.

## Coverage and limits

The report identifies the workflow commit, comparison base, merge base and reviewed head.
Host-side provenance is retained outside the agent's writable input directory.
The report collector rejects symbolic links, empty reports and reports larger than 256 KiB.
The artifact retains the full report for seven days. The Actions summary contains up to 60,000 characters.

The comparison refuses GitHub's 300-file boundary and input larger than 2 MiB to avoid a silently truncated review.
Some binary or large-file patches are unavailable. The report must identify any resulting coverage gaps.
A review does not execute component tests, deployment workflows or Azure validation.
Use the code-review skill linked in [agent guidance](agent-guidance.md) to select those checks separately.

## Update and validate the workflows

The Markdown files are the authored workflows. The `.lock.yml` files are generated and committed beside them.
The shared prompt is `.github/aw/branch-review.md`.
Install [gh-aw v1.0.1](https://github.com/github/gh-aw/releases/tag/v1.0.1) before regenerating the lock files.

```bash
bash .github/scripts/compile-agentic-reviews.sh
cd .github/scripts
npm ci --ignore-scripts
npm test -- --runInBand
```

The compile script also normalises generated diagnostics to the repository's punctuation convention.
The validation workflow checks that compilation leaves the committed lock files unchanged.
Tests cover harness selection, immutable comparisons, input limits, credential exclusion, report provenance and generated job permissions.
