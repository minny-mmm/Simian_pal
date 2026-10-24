# AWS reference demo

คู่มือภาษาไทยสำหรับผู้ใช้และทีม frontend: [วิธีใช้เดโม ข้อจำกัด และการเชื่อม frontend](README_demo_usage.md)

Live demo: https://lvd7t7j977.execute-api.ap-southeast-1.amazonaws.com (login required for data).

Reopened after stop on 24 October 2026 with the existing Lambda/code/config/baseline. The old `a7cklo11v6` API URL in historical handoffs was deleted by stop. Current URL is recorded in `deployment.json`; original delivery evidence remains historical. Post-resume checks are recorded separately in `resume_smoke.json`.

Latest verified state: backend/dashboard routes and **Amazon Bedrock Nova Lite** are deployed; authenticated HTTP analysis/explanation tests passed on real validation. All 33 analysis rows match local output with maximum absolute difference 0.0 at rtol=atol=1e-12. Layer B serves a real Nova Lite response under prompt `reference-thai-grounded-v2`, validated with `reference-text-guard-v2.1`; explicit template fallback remains available on failure. AWS training was not run by this deployment. Final relevant tests: **16 passed**.

AWS-reported peak memory 189 MB out of 512 MB; final measured warm status invocation 71.17 ms. Final client HTTP Analysis 0.58 seconds and cached model explanation 5.94 seconds include network overhead; final status HTTP took 13.93 seconds including cold loading/network. First Nova Lite v2 generation request was observed at 3.38 seconds through HTTP. These are individual measurements, not a latency benchmark; the final explanation read reused the original model response after revalidation, not another generation. API access expires **27 October 2026 08:52 Asia/Bangkok**. Actual metadata/report: `reports/deployment/reference-demo-v1/DEPLOYMENT_HANDOFF.md`, `deployment_validation.json`, `e2e_report.json` and `lambda_measurement.log`.

This deployment serves the existing local real-data baseline from `aws-real-pilot-20261024-v1`. It never fits a baseline or starts a SageMaker job. AWS deployment and AWS training are separate statuses. Original train/source/validator artifacts remain unchanged.

## Runtime and access

`config/reference_demo.yaml` selects one Python 3.12 x86 Lambda (512 MB, 30-second timeout, 512 MB temporary storage) behind an HTTP API. The function serves both the static dashboard and authenticated analysis endpoints, so it needs no local computer running. There is no public S3 hosting and no SageMaker inference endpoint.

Public `/` and `/app.js` contain no patient data. Every `/api/*` request requires a bearer login secret held in AWS Secrets Manager; the password is entered into the browser and retained only in memory, never a URL/localStorage or source code. IAM service credentials for LLM access remain on the server. Responses have no-store headers, no cross-origin permissions, and a restrictive content security policy. Validation participant IDs become VALID-A / VALID-B in API output. No raw signal, roster, original identity or bucket URL is returned to the dashboard or LLM. Do not share login credentials publicly.

The account's current Lambda concurrency limit is 10. AWS reserves 100 unreserved slots when setting positive reserved concurrency, so a per-function reservation of 1 is unavailable in this account. API Gateway throttling is set to 1 request/second, burst 5; authenticated data requests are additionally limited atomically to 100/day with DynamoDB. LLM calls are capped at 20/day, at most 3 metric rows, 8000 prompt UTF-8 bytes and 1000 output tokens/call. Synthetic runtime probes have a separate 40-output-token cap and consume the same daily LLM allowance. Cached results avoid repeated LLM calls. API access expires 72 hours after the initial config release; expiry does not delete billed storage/secrets. Limits are not a hard AWS billing cap or a guarantee against traffic to the public login page.

Measured locally on real validation (33 rows): peak working set about 94 MB; imports 3.2 seconds, computation 5.2 seconds. Initial 512 MB provides room for Linux native libraries, schema validation, S3 reads and SDK overhead. Check `reports/deployment/reference-demo-v1/` for actual AWS duration/memory and end-to-end results after deployment. Local measurement is not an AWS measurement.

## Analysis and explanation

The Lambda package copies scientific `src/` code from the locked source.tar.gz SHA256 `abc089f77af81895c47dd4423e52a03b3d6d24bdd38f5a9bf6ce709a7a6f1421`, not current scientific working-tree code. Only the HTTP adapter/UI are new. Linux wheels in an isolated build directory supply numpy 2.5.1, pandas 2.2.3, PyYAML 6.0.3, jsonschema 4.25.1 and SDK-compatible python-dateutil 2.9.0.post0. No Docker or worker environment changes are needed.

Python 3.12 additionally needs explicit `typing_extensions==4.16.0` for referencing 0.37.0's TypeVar defaults. Cross-building with pip on Python 3.13 did not include that conditional dependency automatically; the first Lambda analysis failed with TypeError. The runtime stack confirmed the cause, the dependency is now pinned/bundled explicitly, and the corrected AWS run passed. Original failure logs are retained separately; no scientific formula or model was changed to fix it. All runtime dependencies are pinned in `aws/demo/requirements.txt` and per-file hashes are recorded in `build_manifest.json`.

The backend downloads the private baseline and two-person valid bundle from S3 and checks all configured SHA256 values before serving. It reuses `load_reference`, `load_bundle`, `build_analysis` and existing public schema 1.0.0. Baseline fit location is explicitly `local`; it is not reported as AWS-trained. Only COPS-7 and NHANES-81262 validation observations are loaded. No test data is opened and no validation is fitted.

`/api/explanation?subject=VALID-A&day=...` uses a 3-row analysis subset. The existing deterministic template is an explicit fallback (`generation_kind=expected_template`), never presented as an LLM result. Current provider config is `config/reference_demo.yaml`: `llm_provider=amazon_bedrock`, `llm_region=ap-southeast-1`, `llm_model=apac.amazon.nova-lite-v1:0`, `llm_auth_mode=runtime_iam_role`. The SDK selects the bedrock-runtime endpoint. No LLM API key/secret or SageMaker endpoint was created; the existing login secret is for dashboard authentication only.

GetInferenceProfile and a real SSO synthetic request verified Nova Lite's exact profile and six destination model ARNs: Singapore, Sydney, Tokyo, Seoul, Osaka and Mumbai. This is APAC cross-Region inference, not Singapore-only execution. CloudTrail metadata lookup did not establish the original Playground session's exact profile; only the profile/routing actually used here is confirmed. See `nova_lite_discovery_probe.json` and `playground_trace_check.json`. IAM simulation before enabling Nova Lite was separate from SSO; actual `/api/llm-probe` invocation then verified the deployed runtime role. `nova_lite_runtime_role_probe.json` is explicitly synthetic and is not the real validation explanation.

Server-side LLM output is recorded separately with model/prompt/guard version, token usage and validation results. Schema/evidence fields are checked against exact analysis; model prose cannot contain numbers. Structured relation codes and required directional/null wording must match signed distances and day delta. Undefined level/magnitude/clinical claims and contradictory directions are rejected. Equivalent Thai negation forms are recognized without changing model wording. These checks still do not prove arbitrary semantic correctness; human review remains required. If the call or validation fails, the UI clearly identifies template fallback.

The first Nova Lite response passed structure but claimed undefined medium/high levels and was rejected during semantic review; it is preserved as `explanation_MODEL_REJECTED_SEMANTICS_v1.json` and its cache was replaced with a template. Prompt v2 produced correct directions/nulls, but the initial exact-text guard rejected equivalent Thai negation. Parser v2.1 fixed that false rejection. The recorded v2 response was revalidated without requesting another explanation generation; its actual model text was retained unchanged, with the same server disclaimer appended. `raw_model_attempt_v2.json`, `revalidated_recorded_model_output.json`, `explanation_MODEL.json` and its validation file preserve that chain. The template before Bedrock is separate in `explanation_TEMPLATE_BEFORE_BEDROCK.json`.

## Build, deploy and inspect

Run from `C:\working\a` using the existing launcher environment. Dependencies are installed into the build target, not into that environment:

```powershell
.venv/Scripts/python.exe -m pip install --platform manylinux_2_27_x86_64 --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --target reports/deployment/reference-demo-v1/build -r aws/demo/requirements.txt
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage build
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage deploy
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage bedrock
.venv/Scripts/python.exe tools/check_reference_demo.py
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage report
```

Do not rebuild over an active deployment without explicitly reviewing a release. The build manifest records exact source/dependency hashes. `release_config.json` locks baseline URI/hash and valid bundle file hashes. The S3 release/config/code objects use content-hash names; existing different content is never overwritten. Deployment state is in `reports/deployment/reference-demo-v1/deployment.json` (URL, ARNs and hashes; no password).

For a reviewed HTTP/UI code correction, `--stage refresh` replaces only those files in a verified deployment ZIP, then `--stage update` updates only the existing function code. Config/data stay pinned. Re-run `tools/check_reference_demo.py` after any actual code or config change. No rebuild/update is needed merely to open the already-deployed URL.

Retrieve the login password privately, for example copy it straight to the clipboard from your authorized terminal:

```powershell
aws secretsmanager get-secret-value --secret-id pd-reference-demo-v1-login --query SecretString --output text --profile minny --region ap-southeast-1 | Set-Clipboard
```

Paste into the demo login form. Do not paste the value into chat or commit it. Do not put it in a URL. The client uses Authorization headers over HTTPS. If rotating the password, recycle Lambda execution environments so the cached old password is invalidated.

## Costs and shutdown

Planning estimate for 72 hours at <=100 authenticated API calls/day: roughly USD 0.05–0.20 for Lambda/API/login-secret/storage/counters/logs, excluding LLM fees, tax, excess traffic and existing resources. AWS Price List for Singapore Nova Lite returns USD 0.000081/1000 input tokens and USD 0.000324/1000 output tokens (On-demand, not Batch). Assuming 20 calls/day at 2000 input + 1000 output tokens each adds about USD 0.00972/day, so a 72-hour combined planning estimate is about USD 0.08–0.23. The accepted recorded generation used 955 input + 349 output tokens, list-rate estimate USD 0.000190431 for that generation only. Rejected generations and synthetic probes can also incur charges. These are not invoices or hard billing caps; credits coverage is **unverified**, never assumed free. See `nova_lite_price_evidence.json` and `deployment_validation.json`. Login-secret/storage fees can continue until cleanup. No provisioned concurrency, VM, NAT gateway or GPU is created.

Stop serving without touching baseline or validator data:

```powershell
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage stop
```

This sets Lambda concurrency to 0 and deletes this demo's API. For complete cleanup after review, delete only function `pd-reference-demo-v1`, DynamoDB table `pd-reference-demo-v1-budget`, secret `pd-reference-demo-v1-login` (use a recovery window), its `/aws/lambda/pd-reference-demo-v1` log group, and the inline policy/role `ParkinsonsReferenceDemoV1`. Remove only `pd-pipeline/deployments/reference-demo-v1/` S3 objects if no longer needed. Do not delete training/reference/validator prefixes or the separate SageMaker role/quota request.

To reopen after `stop`, use the verified existing code/config, without fitting or rebuilding:

```powershell
aws sso login --profile minny --region ap-southeast-1
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage resume
```

Use the URL printed after `RESUMED` (also in `deployment.json`); the deleted API's old URL can return `{"message":"Not Found"}`. Resume verifies the deployed code and release hash, recreates/reuses this demo API with a scoped Lambda invoke permission, then removes the zero-concurrency setting. Baseline, valid bundle, login secret, daily budgets and expiry remain unchanged. Expired releases fail closed. The original `deploy` stage is not a stop/resume command.

## Later baseline replacement and training resume

There is no automatic promotion. A future AWS-trained baseline must pass artifact fingerprint/hash, schema, units, preprocessing/config compatibility and same-input numerical/validation checks first. Create a new release config URI/hash that explicitly states `baseline_fit_location=aws_sagemaker`; review and update Lambda configuration deliberately. The dashboard consumes the same Analysis schema and needs no rewrite. The current deployment routine intentionally only accepts the locked local release; implement/review a promotion command before switching to an AWS artifact.

All training scripts/config/source/input manifests and quota request remain in place. Last observed training state: NOT_SUBMITTED, ml.m5.xlarge applied quota 0; requested 1, CASE_OPENED, case 179280244700132. This deployment does not poll or submit training. Later resume with `tools/run_reference_aws.py --stage preflight`, then `--stage submit` only if quota permits and no matching job exists, and `--stage monitor`. Never automatically retry a failed paid job. See README_reference_baseline.md and the separate AWS execution handoff for training evidence.

References: [Lambda Python packaging](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html), [reserved concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html), [Lambda pricing](https://aws.amazon.com/lambda/pricing/), [Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/), [API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/).
