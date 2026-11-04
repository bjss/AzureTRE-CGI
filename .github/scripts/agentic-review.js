const fs = require('node:fs');
const path = require('node:path');
const { withReasoningEffort } = require('./agentic-review-copilot.cjs');

const REVIEW_DIRECTORY = '/tmp/gh-aw/review';
const MAX_INPUT_BYTES = 2 * 1024 * 1024;
const MAX_REPORT_BYTES = 256 * 1024;

function selectHarness(value = '') {
  const harness = value || 'copilot';
  if (!['copilot', 'codex'].includes(harness)) {
    throw new Error('AGENT_HARNESS must be copilot or codex.');
  }
  return harness;
}

function validateSettings(env) {
  const harness = selectHarness(env.REVIEW_HARNESS);
  if (harness === 'copilot') {
    withReasoningEffort([], env.COPILOT_REASONING_EFFORT);
    return;
  }
  const effort = env.CODEX_REASONING_EFFORT || 'max';
  if (!['none', 'low', 'medium', 'high', 'xhigh', 'max'].includes(effort)) {
    throw new Error('CODEX_REASONING_EFFORT must be none, low, medium, high, xhigh or max.');
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(env.CODEX_MODEL || 'gpt-6-luna')) {
    throw new Error('CODEX_MODEL must be a model identifier without whitespace or shell syntax.');
  }
}

function validateRef(value, name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/.test(value || '') || value.includes('..')) {
    throw new Error(`${name} must be a branch, tag or commit without whitespace or shell syntax.`);
  }
  return value;
}

function validateSha(value) {
  if (!/^[a-f0-9]{40}$/.test(value || '')) {
    throw new Error('GitHub did not return a valid immutable commit SHA.');
  }
  return value;
}

async function prepareReview({ github, context, env, directory = REVIEW_DIRECTORY }) {
  validateSettings(env);
  const { owner, repo } = context.repo;
  const number = env.REVIEW_PR_NUMBER || '';
  let baseSha;
  let headSha;
  let headRepository = `${owner}/${repo}`;
  if (number) {
    if (!/^[1-9][0-9]{0,8}$/.test(number)) {
      throw new Error('The pull request number must be a positive integer.');
    }
    if (env.REVIEW_TARGET_REF) {
      throw new Error('Supply a pull request number or target_ref, not both.');
    }
    const { data: pullRequest } = await github.rest.pulls.get({ owner, repo, pull_number: Number(number) });
    baseSha = validateSha(pullRequest.base.sha);
    headSha = validateSha(pullRequest.head.sha);
    headRepository = pullRequest.head.repo?.full_name || headRepository;
  } else {
    const baseRef = validateRef(env.REVIEW_BASE_REF || 'main', 'base_ref');
    const headRef = validateRef(env.REVIEW_TARGET_REF, 'target_ref');
    const commits = await Promise.all([
      github.rest.repos.getCommit({ owner, repo, ref: baseRef }),
      github.rest.repos.getCommit({ owner, repo, ref: headRef }),
    ]);
    baseSha = validateSha(commits[0].data.sha);
    headSha = validateSha(commits[1].data.sha);
  }

  const { data: comparison } = await github.rest.repos.compareCommitsWithBasehead({
    owner, repo, basehead: `${baseSha}...${headSha}`, per_page: 1,
  });
  // GitHub returns at most 300 changed files. Refuse a possibly incomplete review.
  if (!Array.isArray(comparison.files) || comparison.files.length >= 300) {
    throw new Error('The comparison file list is missing or reached GitHub\'s 300-file limit. Split the review.');
  }
  const input = {
    repository: `${owner}/${repo}`,
    head_repository: headRepository,
    workflow_sha: validateSha(context.sha),
    pull_request_number: number ? Number(number) : null,
    base_sha: baseSha,
    merge_base_sha: validateSha(comparison.merge_base_commit?.sha),
    head_sha: headSha,
    comparison_status: comparison.status,
    files: comparison.files.map(file => ({
      filename: file.filename,
      previous_filename: file.previous_filename,
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
      patch: file.patch ?? null,
    })),
  };
  const content = `${JSON.stringify(input, null, 2)}\n`;
  if (Buffer.byteLength(content) > MAX_INPUT_BYTES) {
    throw new Error('The comparison exceeds the 2 MiB review-input limit. Split the review.');
  }
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'input.json'), content, { flag: 'wx', mode: 0o600 });
  // This host-side copy is mounted read-only in the agent sandbox.
  const provenanceDirectory = path.join(env.RUNNER_TEMP, 'gh-aw');
  fs.mkdirSync(provenanceDirectory, { recursive: true });
  fs.writeFileSync(path.join(provenanceDirectory, 'review-provenance.json'), content, { flag: 'wx', mode: 0o600 });
  return input;
}

function collectReport(env, directory = REVIEW_DIRECTORY) {
  const expectedDirectory = path.join(fs.realpathSync(path.dirname(directory)), path.basename(directory));
  if (fs.realpathSync(directory) !== expectedDirectory) {
    throw new Error('The review directory must not be a symbolic link.');
  }
  const descriptor = fs.openSync(path.join(directory, 'report.md'), fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  let report;
  try {
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size === 0 || stat.size > MAX_REPORT_BYTES) {
      throw new Error('The review report must be a non-empty regular file of at most 256 KiB.');
    }
    report = fs.readFileSync(descriptor, 'utf8');
  } finally {
    fs.closeSync(descriptor);
  }
  const input = JSON.parse(fs.readFileSync(path.join(env.RUNNER_TEMP, 'gh-aw', 'review-provenance.json'), 'utf8'));
  const provenance = `# Agentic branch review\n\nRepository: ${input.repository}\n\n` +
    `Workflow: \`${input.workflow_sha}\`\n\nBase: \`${input.base_sha}\`\n\n` +
    `Merge base: \`${input.merge_base_sha}\`\n\nHead: \`${input.head_sha}\`\n\n`;
  const fullReport = provenance + report;
  const destination = path.join(env.RUNNER_TEMP, 'agentic-review-report');
  fs.mkdirSync(destination, { recursive: true });
  fs.writeFileSync(path.join(destination, 'report.md'), fullReport);
  fs.appendFileSync(env.GITHUB_STEP_SUMMARY, `${fullReport.slice(0, 60000)}\n\nThe artifact contains the full report.\n`);
}

module.exports = { selectHarness, validateSettings, prepareReview, collectReport };
