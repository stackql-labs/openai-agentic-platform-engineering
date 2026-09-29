/**
 * Entitlements audit CLI.
 *
 *   npm run setup    [-- --idp entra_id|okta]      pull providers, print server_info
 *   npm run sweep    [-- --dry-run] [--idp ...]    the recertification sweep
 *   npm run validate                               validate_select_query over queries/
 *
 * Runs on a schedule (cron or a GitHub Actions cron workflow); no daemon, no chat loop.
 */

import { parseArgs } from 'node:util';
import { ConfigError, IDP_PROVIDERS } from './config.ts';

const HELP = `usage: npm run <setup|sweep|validate> [-- options]
   or: npx tsx src/main.ts <setup|sweep|validate> [options]

commands
  setup      pull the providers (aws, azure, google, github and the IdP) into STACKQL_APPROOT
             through the MCP tool pull_provider, then print server_info
  sweep      run the read-only recertification sweep: findings -> console table + brief,
             runs/entitlements-<ts>.json and runs/entitlements-recertification-<ts>.md,
             then the cost and trace block
  validate   plan every SELECT under entitlements/queries/ with validate_select_query

options
  --idp <${IDP_PROVIDERS.join('|')}>     IdP provider for this run (default: IDP_PROVIDER or entra_id)
  --dry-run                 sweep only: print the query pack and the tool list the model would
                            see, validate the static SELECTs, call no model, write nothing
  -h, --help                this text

environment: read from the repo-root .env (see .env.example); model ids come from SWEEP_MODEL and
REASONING_MODEL, tenancy from AWS_ACCOUNT_ID, AZURE_SUBSCRIPTION_ID, GOOGLE_PROJECT, GITHUB_ORG,
AZURE_TENANT_ID (entra_id) or OKTA_DOMAIN (okta).`;

async function main(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      'dry-run': { type: 'boolean', default: false },
      idp: { type: 'string' },
      help: { type: 'boolean', short: 'h', default: false },
    },
  });
  const cmd = positionals[0];
  if (values.help || !cmd) {
    console.log(HELP);
    return values.help ? 0 : 2;
  }
  switch (cmd) {
    case 'setup': {
      const { runSetup } = await import('./setup.ts');
      return runSetup({ idp: values.idp });
    }
    case 'sweep': {
      const { runSweep } = await import('./sweep.ts');
      return runSweep({ dryRun: values['dry-run'], idp: values.idp });
    }
    case 'validate': {
      const { runValidate } = await import('./validate.ts');
      return runValidate();
    }
    default:
      console.error(`unknown command '${cmd}'\n\n${HELP}`);
      return 2;
  }
}

main(process.argv.slice(2))
  .then((code) => {
    process.exitCode = code;
  })
  .catch((e: unknown) => {
    if (e instanceof ConfigError) console.error(`config error: ${e.message}`);
    else console.error(e);
    process.exitCode = 1;
  });
