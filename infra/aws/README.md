# AWS execution artifacts (M4.3)

Reviewed, version-controlled inputs for the AWS SSM adapter. **None of this has been created in
AWS or run on an instance by the build** (`PROGRESS.md` §8). Placeholders (`REGION`,
`ACCOUNT_ID`, `MANAGEMENT_ACCOUNT_ID`, `DCP_RUNTIME_ROLE_NAME`) must be replaced by the owner.

| File | What it is |
|---|---|
| `ssm/DCP-ServiceControl.json` | The registered SSM document. The only place a shell line exists. Security-critical: change it only with a review of `backend/tests/providers/aws_artifacts/`. |
| `iam-execution-policy.json` | Permissions of the per-account **execution role** DCP assumes: `SendCommand` only on the one document and only on instances tagged `DcpManaged=true`; an explicit **Deny** on `AWS-RunShellScript` and `AWS-RunPowerShellScript`. |
| `trust-policy.json` | Who may assume the execution role: DCP's runtime role only. |
| `../../scripts/verify_aws_role.py` | Operator-run proof (IAM policy simulator) that the deny holds. |

DCP's own runtime role (instance or task role) needs `sts:AssumeRole` on the execution role's ARN
and nothing else in the target account. **No access key may exist** in DCP's environment: the
adapter refuses to start when `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` is set.

## Manual real-integration check (non-production only)

This is the only evidence for the milestone's acceptance criterion. Production is never used.

1. Create the execution role from `trust-policy.json` and `iam-execution-policy.json` (placeholders
   replaced). Note the role ARN.
2. Create the document and note its version:
   `aws ssm create-document --name DCP-ServiceControl --document-type Command --document-format JSON --content file://infra/aws/ssm/DCP-ServiceControl.json`
3. Tag one test instance `DcpManaged=true`. Confirm it is SSM-managed (`aws ssm describe-instance-information`)
   and that its SSM Agent is recent enough for `interpolationType: ENV_VAR` (AWS documents 3.3.2746.0 or
   later; confirm against the current AWS page).
4. Set `DCP_AWS_SSM_DOCUMENT_VERSION` to that version. Give DCP's runtime an ambient role that may
   assume the execution role. No key variables.
5. Register the account (`POST /api/v1/cloud-accounts`, `credential_reference` = the role ARN) and
   verify it (`POST …/verify`). Register the instance as a server and one unit on it.
6. Request a restart through the UI. Watch it reach `SUCCEEDED` with `observed_state_after`.
7. `python scripts/verify_aws_role.py --role-arn … --region …` must print `OK`.
8. `cd backend && uv run pytest -m real_integration --integration-profile=nonprod-sandbox`
   (see `tests/providers/aws/test_real_integration.py` for the environment it reads).
9. Record date, account class, instance type, agent version and every result in `PROGRESS.md` as a
   **manual check**.
