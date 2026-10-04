# DeepSWE runtime repairs

These changes apply to new evaluations using the working-tree adapter and DeepSWE runners.
No Diffex package release is required; the runtime remains pinned to Diffex 0.3.2.
Existing trial results and the pinned dataset have not been modified.

## Implemented

- DeepSWE explicitly selects `task_environment: preserve`.
  The launcher preserves task environment variables and HOME-dependent dependency caches while keeping Diffex settings, sessions, and mutable state in a fresh run directory.
  Inherited Diffex controls and unselected provider credentials are excluded.
- Both E2B sandboxes receive the published task image's environment and working directory explicitly.
  Image metadata comes from public ECR without downloading filesystem layers, and configuration and manifest digests are checked.
  Explicit task and command environment overrides keep precedence.
  Startup verifies propagation and saves evidence under `artifacts/e2b-runtime/` before model execution or grading.
- Verifier shutdown attempts a bounded download before sandbox deletion, including after a timeout.
  Partial logs and recovery status are saved under `artifacts/verifier-recovery/`, separately from completed grading output.
  Download failures remain explicit and do not prevent sandbox deletion.
- Full, sample, and smoke runners use new preparation directories ending in `runtime-v3`, avoiding reuse of previously prepared launcher settings.
  Smoke runs retain the complete adapter source, and smoke qualification requires environment checks for both sandboxes.
- Two adapter tests with obsolete model/reasoning expectations now validate the selected configuration.

## Validation

The real launcher was reproduced in an offline Linux container with a fixture CLI.
Before the patch, a child Python process failed with `ModuleNotFoundError` despite a supplied PYTHONPATH.
After the patch, imports, explicit cache paths, HOME-dependent caches, credential filtering, and fresh Diffex settings passed.
The existing launcher isolation regression also passed.

Public ECR metadata confirmed `DENO_DIR=/deno-cache` for Cliffy and `PYTHONPATH=/app/src` for PSD Tools.
Fifteen offline E2B tests cover image configuration integrity, platform selection, agent/verifier environment propagation through actual child processes, override precedence, fail-fast behavior, verifier log recovery, registry retries, and prepared catalogs.
The adapter Python suite and `npm run check` passed.
The full configuration selects all 113 tasks with one attempt and concurrency eight.

No live E2B sandbox or model call was started during this repair.
E2B credentials were not available in the session, so Cliffy and PSD Tools still need live verifier confirmation before treating their environment failures as resolved end to end.
Cliffy's cache contents have not been independently checked; preserving DENO_DIR cannot repair an incomplete published cache.

## Remaining issues

| Task | Remaining issue |
| --- | --- |
| Cliffy | Runtime propagation is patched; offline cache completeness and successful live verification remain unconfirmed. |
| PSD Tools | Runtime propagation is patched; live verification and solution correctness remain unconfirmed. |
| Pwntools | The cause of the historical 1,800-second timeout is unknown because the old verifier logs were not retained; future shutdowns now attempt to preserve them. |
| Obsidian TOC grading | The whitelist uses `Auto Table of Contents`, while the retained reporter output uses `Auto TOC`; its new-suite report contains 37 passes and four failures. |
| Aiomonitor | Reported solution failure: snapshot endpoints return 404. |
| Bandit | Reported solution failure: suppression regions and skipped-test metrics. |
| Clack | Reported solution failure: caching, cancellation, retry timing, and async UI behavior. |
| Etree | Reported solution failure: whitespace and patch parent paths. |
| FastAPI | Reported solution failure: non-deprecated route tracking. |
| Go Critic | Reported solution failure: documentation-link checker assertions. |
| Koota predicates | Reported solution failure: Changed and Removed predicates. |
| Obsidian TOC solution | Four formatting failures remain even with the test-ID mapping corrected. |
| Onedump | Reported solution failure: encryption key loading and derivation. |
| Pest | Reported solution failure: 19 character-class optimization tests. |
| Prometheus reload | Reported solution failure: fractional timings where tests expect integers. |
| Python Statemachine | Reported solution failure: state-data defaults and SCXML. |
| Vulture | Reported solution failure: cache backup/metadata-file assertion. |

The reported solution failures do not establish a general Diffex implementation defect.
Task-specific solutions or hidden-test hints were not added to the eval harness.
Obsidian's scoring correction requires a separately versioned dataset correction or an explicitly identified local grading variant; the pinned benchmark's test IDs and rewards remain unchanged.
Preparation also reports an upstream overlapping-artifact warning for `/logs/artifacts` and `/logs/artifacts/model.patch`; the pinned task definitions remain unchanged.

## Next run

From the repository root, use `zsh evals/deepswe/run-full.zsh --print-config` to inspect the newly prepared job.
Use a fresh preparation directory if overriding `DS_PREPARED`; old prepared configurations deliberately fail comparison rather than silently changing an evaluation.
Run live smoke/verifier qualification with the normal credentials before starting the next full evaluation.

## Login-shell startup regression

The first 113-task, one-attempt run failed during sandbox startup because the new environment check rejected PATH.
E2B 2.51 constructs commands as `/bin/bash -l -c`, allowing login profiles to replace PATH after it is supplied through the SDK environment.
The original local environment regression used a non-login shell and missed this behavior.
The backend now restores the fully merged PATH inside the login shell before executing each command, preserving image, per-command, and scoped override precedence.
Provider credentials remain in the SDK environment parameter rather than shell command text.
The strict environment check remains enabled.

A regression now exercises the pinned E2B SDK's actual command construction and executes the resulting shell process locally with a profile that replaces PATH.
It failed before the fix with `task-only-tool: command not found` and passes after the fix, including paths containing spaces, quotes, and dollar signs.
The login-shell regression passes alongside the other E2B regression tests.
Live E2B validation of this follow-up still requires credentials in the launching terminal.
The next full run remains Diffex 0.3.2, GPT-6 Luna at max thinking, subagents disabled, evolution disabled, 113 tasks with one attempt each, and concurrency eight.

## Public registry throttling regression

The subsequent run failed before creating sandboxes because concurrent image metadata requests received HTTP 429 from public ECR.
Harbor's subsequent log download reported `Sandbox not found` because startup had never created one.
The overlapping-artifact warnings are independent of this failure.

Image runtime metadata is now collected serially during preparation with paced requests, bounded retries for transient failures, and support for `Retry-After`.
Successful reads are checkpointed atomically so interrupted preparation resumes without repeating them.
Concurrent preparers for the same catalog share a file lock.
Preparation writes a catalog with integrity checks and pins its SHA-256 in the job configuration.
Both agent and verifier startup load that catalog locally, eliminating these public ECR metadata requests from concurrent trial startup.
Each runner archives the catalog with the run's configuration and source snapshots.

Regression tests reproduce a throttled manifest response, validate bounded retries, reject modified catalogs, resume partial preparation, and exercise eight concurrent starts through Harbor's real environment factory without public registry requests.
Live public ECR preparation successfully cached all 113 image configurations in `prepared-113x1-c8-runtime-v3`.
An offline check then loaded all 113 agent and 113 verifier configurations through Harbor's factory and reused the complete catalog with zero network requests.
All 15 E2B regressions, runner syntax checks, Harbor configuration rendering, and `npm run check` passed.
E2B still performs its own image provisioning, which this host-side metadata fix does not validate.
