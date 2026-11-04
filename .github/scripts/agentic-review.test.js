const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const yaml = require('js-yaml');
const { selectHarness, validateSettings, prepareReview, collectReport } = require('./agentic-review');
const { withReasoningEffort } = require('./agentic-review-copilot.cjs');

const BASE = 'a'.repeat(40);
const HEAD = 'b'.repeat(40);
const MERGE_BASE = 'c'.repeat(40);
const WORKFLOW = 'd'.repeat(40);
let temporary;
let directory;
let env;
let github;
const context = { repo: { owner: 'example', repo: 'tre' }, sha: WORKFLOW };

beforeEach(() => {
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'agentic-review-test-'));
  directory = path.join(temporary, 'review');
  env = {
    RUNNER_TEMP: path.join(temporary, 'runner'),
    GITHUB_STEP_SUMMARY: path.join(temporary, 'summary.md'),
    REVIEW_TARGET_REF: 'feature/review',
  };
  github = { rest: {
    pulls: { get: jest.fn().mockResolvedValue({ data: { base: { sha: BASE }, head: { sha: HEAD } } }) },
    repos: {
      getCommit: jest.fn().mockResolvedValueOnce({ data: { sha: BASE } }).mockResolvedValueOnce({ data: { sha: HEAD } }),
      compareCommitsWithBasehead: jest.fn().mockResolvedValue({ data: {
        merge_base_commit: { sha: MERGE_BASE }, status: 'ahead',
        files: [{ filename: 'api_app/example.py', status: 'modified', patch: '@@ -1 +1 @@\n-old\n+new' }],
      } }),
    },
  } };
});

afterEach(() => fs.rmSync(temporary, { recursive: true, force: true }));

test('Copilot is selected without settings and unsupported harnesses fail', () => {
  expect(selectHarness()).toBe('copilot');
  expect(selectHarness('codex')).toBe('codex');
  expect(() => selectHarness('auto')).toThrow('AGENT_HARNESS');
});

test('Copilot effort is omitted when unset and passed separately when configured', () => {
  const args = ['copilot', '--prompt-file', '/tmp/prompt'];
  expect(withReasoningEffort(args)).toBe(args);
  expect(withReasoningEffort(args, 'high')).toEqual([...args, '--reasoning-effort=high']);
  expect(() => withReasoningEffort(args, 'high; echo secret')).toThrow('COPILOT_REASONING_EFFORT');
});

test('invalid Codex configuration fails, while settings for an inactive engine are ignored', () => {
  expect(() => validateSettings({ REVIEW_HARNESS: 'codex' })).not.toThrow();
  expect(() => validateSettings({ REVIEW_HARNESS: 'codex', CODEX_REASONING_EFFORT: 'ultra' })).toThrow();
  expect(() => validateSettings({ REVIEW_HARNESS: 'codex', CODEX_MODEL: 'model\nsecret' })).toThrow();
  expect(() => validateSettings({ REVIEW_HARNESS: 'copilot', CODEX_REASONING_EFFORT: 'invalid' })).not.toThrow();
});

test('branch refs are resolved once and only immutable SHAs enter the comparison', async () => {
  env.OPENAI_API_KEY = 'not-a-real-key';
  const input = await prepareReview({ github, context, env, directory });
  expect(github.rest.repos.compareCommitsWithBasehead).toHaveBeenCalledWith({
    owner: 'example', repo: 'tre', basehead: `${BASE}...${HEAD}`, per_page: 1,
  });
  expect(input).toMatchObject({ base_sha: BASE, head_sha: HEAD, merge_base_sha: MERGE_BASE, workflow_sha: WORKFLOW });
  const content = fs.readFileSync(path.join(directory, 'input.json'), 'utf8');
  expect(content).not.toContain(env.OPENAI_API_KEY);
  expect(fs.readFileSync(path.join(env.RUNNER_TEMP, 'gh-aw/review-provenance.json'), 'utf8')).toBe(content);
});

test('a PR uses its own immutable base and head without resolving mutable branch names', async () => {
  env.REVIEW_PR_NUMBER = '12';
  env.REVIEW_TARGET_REF = '';
  await prepareReview({ github, context, env, directory });
  expect(github.rest.pulls.get).toHaveBeenCalledWith({ owner: 'example', repo: 'tre', pull_number: 12 });
  expect(github.rest.repos.getCommit).not.toHaveBeenCalled();
});

test.each([
  { REVIEW_TARGET_REF: '' },
  { REVIEW_TARGET_REF: '--upload-pack=evil' },
  { REVIEW_PR_NUMBER: '12; echo secret' },
  { REVIEW_PR_NUMBER: '12', REVIEW_TARGET_REF: 'feature/review' },
])('rejects an invalid or ambiguous target: %j', async values => {
  await expect(prepareReview({ github, context, env: { ...env, ...values }, directory })).rejects.toThrow();
  expect(github.rest.repos.compareCommitsWithBasehead).not.toHaveBeenCalled();
});

test('refuses a potentially truncated comparison instead of issuing an incomplete review', async () => {
  github.rest.repos.compareCommitsWithBasehead.mockResolvedValue({ data: {
    merge_base_commit: { sha: MERGE_BASE }, files: Array(300).fill({ filename: 'file' }),
  } });
  await expect(prepareReview({ github, context, env, directory })).rejects.toThrow('300-file limit');
});

test('missing patches remain explicit and oversized input fails before inference', async () => {
  github.rest.repos.compareCommitsWithBasehead.mockResolvedValueOnce({ data: {
    merge_base_commit: { sha: MERGE_BASE }, files: [{ filename: 'image.png', status: 'added' }],
  } });
  const input = await prepareReview({ github, context, env, directory });
  expect(input.files[0].patch).toBeNull();
  fs.rmSync(directory, { recursive: true });
  github.rest.repos.getCommit.mockResolvedValue({ data: { sha: HEAD } });
  github.rest.repos.compareCommitsWithBasehead.mockResolvedValueOnce({ data: {
    merge_base_commit: { sha: MERGE_BASE }, files: [{ filename: 'large', patch: 'x'.repeat(3 * 1024 * 1024) }],
  } });
  await expect(prepareReview({ github, context, env, directory })).rejects.toThrow('2 MiB');
});

test('report provenance comes from the read-only host copy, even if the agent changes its input', async () => {
  await prepareReview({ github, context, env, directory });
  fs.writeFileSync(path.join(directory, 'input.json'), JSON.stringify({ head_sha: 'forged' }));
  fs.writeFileSync(path.join(directory, 'report.md'), 'No actionable findings.\n');
  collectReport(env, directory);
  const report = fs.readFileSync(path.join(env.RUNNER_TEMP, 'agentic-review-report/report.md'), 'utf8');
  expect(report).toContain(HEAD);
  expect(report).toContain('No actionable findings.');
  expect(report).not.toContain('forged');
});

test('report collection refuses symbolic links to host files', async () => {
  await prepareReview({ github, context, env, directory });
  const outside = path.join(temporary, 'outside');
  fs.writeFileSync(outside, 'must not be published');
  fs.symlinkSync(outside, path.join(directory, 'report.md'));
  expect(() => collectReport(env, directory)).toThrow();
  expect(fs.existsSync(path.join(env.RUNNER_TEMP, 'agentic-review-report'))).toBe(false);
});

test('compiled jobs use the selected environment and grant no GitHub mutation permissions', () => {
  for (const engine of ['copilot', 'codex']) {
    const file = path.join(__dirname, `../workflows/agentic-review-${engine}.lock.yml`);
    const workflow = yaml.load(fs.readFileSync(file, 'utf8'));
    for (const job of Object.values(workflow.jobs)) {
      expect(job.environment).toBe('${{ inputs.environment }}');
      for (const [scope, level] of Object.entries(job.permissions || {})) {
        if (scope !== 'copilot-requests') expect(level).not.toBe('write');
      }
    }
    if (engine === 'codex') {
      expect(workflow.on.workflow_call.secrets.OPENAI_API_KEY.required).toBe(true);
      expect(workflow.jobs.agent.steps.some(step => step.env?.OPENAI_API_KEY?.includes('secrets.OPENAI_API_KEY'))).toBe(true);
      const execution = workflow.jobs.agent.steps.find(step => step.name === 'Execute Codex CLI').run;
      expect(execution).toContain('--exclude-env CODEX_API_KEY');
      expect(execution).toContain('--exclude-env OPENAI_API_KEY');
      expect(execution).toContain('/gh-aw/actions/codex_harness.cjs');
      expect(execution).not.toContain('review_codex_harness.cjs');
    }
  }
});

test('the native router validates the environment secret before calling Codex', () => {
  const file = path.join(__dirname, '../workflows/agentic-review.yml');
  const workflow = yaml.load(fs.readFileSync(file, 'utf8'));
  const preflight = workflow.jobs['codex-preflight'];
  expect(preflight.environment).toBe('${{ inputs.environment }}');
  expect(preflight.steps[0].env.OPENAI_API_KEY).toBe('${{ secrets.OPENAI_API_KEY }}');
  expect(preflight.outputs).toBeUndefined();
  expect(workflow.jobs.codex.needs).toContain('codex-preflight');
  expect(workflow.jobs.codex.secrets).toEqual({ OPENAI_API_KEY: '${{ secrets.OPENAI_API_KEY }}' });
});
