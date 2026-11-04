const { spawnSync } = require('node:child_process');
const path = require('node:path');

function withReasoningEffort(args, effort = '') {
  if (!effort) return args;
  if (!['low', 'medium', 'high', 'xhigh', 'max'].includes(effort)) {
    throw new Error('COPILOT_REASONING_EFFORT must be low, medium, high, xhigh or max.');
  }
  return [...args, `--reasoning-effort=${effort}`];
}

if (require.main === module) {
  // Retain the gh-aw harness, including its tool policy, retries and timeouts.
  const args = withReasoningEffort(process.argv.slice(2), process.env.REVIEW_COPILOT_REASONING_EFFORT);
  const result = spawnSync(process.execPath, [path.join(__dirname, 'copilot_harness.cjs'), ...args], {
    stdio: 'inherit',
    env: process.env,
  });
  if (result.error) throw result.error;
  process.exit(result.status === null ? 1 : result.status);
}

module.exports = { withReasoningEffort };
