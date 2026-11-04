const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const PROXY_PLACEHOLDER = 'gh-aw-openai-proxy-placeholder';

function withProxyCredential(env, config, reflection) {
  if (env.CODEX_API_KEY) return env;
  const section = config.match(/\[model_providers\.openai-proxy\]([\s\S]*?)(?:\n\[|$)/)?.[1] || '';
  const base = section.match(/(?:^|\n)\s*base_url\s*=\s*"([^"]+)"/)?.[1];
  const endpoint = base ? new URL(base) : null;
  const proxyHosts = ['api-proxy', 'host.docker.internal', '172.30.0.30', '127.0.0.1'];
  const configured = reflection?.endpoints?.some(item =>
    item.configured === true && item.provider === 'openai' && Number(item.port) === Number(endpoint?.port));
  if (env.AWF_REFLECT_ENABLED !== '1' || env.GH_AW_LLM_PROVIDER !== 'openai' ||
      !/^model_provider\s*=\s*"openai-proxy"\s*$/m.test(config) ||
      !/^requires_openai_auth\s*=\s*false\s*$/m.test(section) ||
      endpoint?.protocol !== 'http:' || !proxyHosts.includes(endpoint.hostname) || !configured) {
    throw new Error('Codex proxy startup requires a configured OpenAI AWF sidecar and matching local provider configuration.');
  }
  // The sidecar holds the real key and replaces this non-secret compatibility value.
  return { ...env, CODEX_API_KEY: PROXY_PLACEHOLDER };
}

async function main() {
  let env = process.env;
  if (!env.CODEX_API_KEY) {
    const configPath = env.CODEX_HOME ? path.join(env.CODEX_HOME, 'config.toml') : env.GH_AW_MCP_CONFIG;
    const config = fs.readFileSync(configPath, 'utf8');
    const { fetchAWFReflect } = require('./awf_reflect.cjs');
    const result = await fetchAWFReflect({ logger: message => process.stderr.write(`[review-codex] ${message}\n`) });
    env = withProxyCredential(env, config, result.ok ? result.reflectData : null);
  }
  const result = spawnSync(process.execPath, [path.join(__dirname, 'codex_harness.cjs'), ...process.argv.slice(2)], {
    stdio: 'inherit', env,
  });
  if (result.error) throw result.error;
  process.exitCode = result.status === null ? 1 : result.status;
}

if (require.main === module) {
  main().catch(error => {
    process.stderr.write(`[review-codex] ${error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { withProxyCredential, PROXY_PLACEHOLDER };
