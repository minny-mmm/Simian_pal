# AWS vs local reference — compare-pilot-local-py312-vs-ckpt01

**ผล: `new_version_more_train`** — 5 more train participants, existing values unchanged

| | local (ส่งไป) | AWS |
| --- | --- | --- |
| file_sha256 | `cb844f813f2adadb4fcb84dee4beaeaf60dd7ec3b79fe2df02ffc7226da760de` | `4b623bcecb3f5298c56e24a5c56fcf9e9a2b60ab77215d36b4521f83a3c9b945` |
| baseline_sha256 | `cb844f813f2adadb4fcb84dee4beaeaf60dd7ec3b79fe2df02ffc7226da760de` | `4b623bcecb3f5298c56e24a5c56fcf9e9a2b60ab77215d36b4521f83a3c9b945` |
| artifact_id | `a6918f7e2cd1d86cee68dea927b2c8ed8bab96054b498620170d8d1fcb704996` | `9b65d11f9b86263b3a24eaae154657b72324302be5a4e148108c6f1f0510b76e` |
| baseline_version | `real-cohort-reference-real-20261023-v1-checkpoint-01` | `real-data-pilot-aws-real-pilot-20261024-v1` |
| created_utc | `2026-10-23T05:34:31.469244+00:00` | `2026-10-23T17:43:30.937692+00:00` |
| git_commit | `21f84853c6fe75c61ce69df0f71a898786ad3e27` | `7989ba8dba461122b3aa750c8ea7501f1382a5d9` |
| git_dirty | `True` | `True` |

| field | equal | local | aws |
| --- | --- | --- | --- |
| preprocessing_version | True | `a0417b7e45703d480f6cbcac02790b8290a14286` | `a0417b7e45703d480f6cbcac02790b8290a14286` |
| feature_contract | True | `mew-37d5722-reference-v1` | `mew-37d5722-reference-v1` |
| metrics | True | `{'enmo_hourly_median_mean_g': {'unit': '` | `{'enmo_hourly_median_mean_g': {'unit': '` |
| data_kind | True | `real` | `real` |
| fit_split | True | `train` | `train` |
| roster_hash | True | `60d706bc34ba55fbed0a8fa5b2816dff0242ecc3` | `60d706bc34ba55fbed0a8fa5b2816dff0242ecc3` |
| schema_version | True | `1` | `1` |
| kind | True | `separate_cohort_reference_not_diagnosis` | `separate_cohort_reference_not_diagnosis` |
| policy_config_hash | True | `5f0ba71952adbfc831dbce51c3b3da5c69931357` | `5f0ba71952adbfc831dbce51c3b3da5c69931357` |
| exact_config_hash | False | `8f4e0eb6fe615fd06ba66faba2cee06b01f7cec0` | `ac7c04820059bac20312bc47eed406ee2cd3ef07` |
| training_daily_hash | False | `cac738117ec96eebb1c26fd4e098f121fe1266a3` | `a5dbb201c8d924c32cb54158c09f1e8c418193ed` |

- entries local/AWS: {'local': 158, 'aws': 249}, เพิ่ม 91, หาย 0, ค่าเปลี่ยน 0
- คน train ใหม่: [['cops', 'COPS-13'], ['cops', 'COPS-16'], ['nhanes', 'NHANES-73746'], ['nhanes', 'NHANES-73781'], ['nhanes', 'NHANES-73899']]
- summary ที่ต่าง: 50
- validation: {'status': 'revalidated', 'valid_bundle': 'backtest\\workspace\\real\\data\\daily\\reference-v1\\aws-real-pilot-20261024-v1\\valid', 'result': {'run_id': 'compare-pilot-local-py312-vs-ckpt01-validate', 'overall': 'pass_with_unevaluable', 'output': 'D:\\AWS\\Simianpal\\backtest\\outputs\\compare-pilot-local-py312-vs-ckpt01-validate', 'audit': {'baseline_lock': 'pass', 'hash_chain': 'pass', 'reference_formulas': 'pass', 'distances': 'pass', 'null_handling': 'pass', 'previous_day': 'pass', 'coverage': 'pass', 'official_reports': 'not_evaluable', 'test_lock': 'pass'}}}
