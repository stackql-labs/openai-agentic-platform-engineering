## Entitlements demo estate

Two small stackql-deploy stacks make the sweep's findings real. Both are read back by the same
StackQL MCP server the agent uses; build and teardown run from the repo root with the shared .env.

- `stack/aws` - an IAM user `<DEMO_PREFIX>-admin` with AdministratorAccess attached, tagged, with
  no access key and no console password. Cost: none.
- `stack/entra_id` - a security group `<DEMO_PREFIX>-cloud-admins` and a disabled user
  `<DEMO_PREFIX>-leaver` that is a member of it. Cost: none. Needs Graph application permissions
  with admin consent (see the manifest comments) and two extra variables, ENTRA_TENANT_DOMAIN and
  ENTRA_LEAVER_INITIAL_PASSWORD.

Not planted, on purpose:

- Azure Owner at subscription scope: writing a role assignment needs User Access Administrator
  or Owner on the subscription, which the demo service principal should not hold. The sweep
  reports whatever Owner assignments already exist at subscription scope.
- GCP roles/owner: adding a binding means rewriting the project IAM policy (setIamPolicy), which
  is not worth the risk on a shared demo project. The sweep reports the roles/owner bindings that
  already exist.
- GitHub: org members, outside collaborators and repos are read as they are; an outside
  collaborator with admin on a `<DEMO_PREFIX>-*` repo is reported if one exists.
- Okta: no stack. With IDP_PROVIDER=okta the sweep reads the Okta org as it is; create a group
  named `<DEMO_PREFIX>-cloud-admins` with a deprovisioned member to reproduce the critical finding.

Column names and method shapes were taken from `SHOW INSERT INTO` / `SHOW METHODS IN` on the
pulled providers; the stacks were not executed live in the build environment (no credentials).
