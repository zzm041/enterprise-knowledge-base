# Independent Same-GPU Model Evaluation Implementation Plan

> Historical plan, closed after the accepted 2026-10-06 fail-closed run: 17/24 real calls, 16 success / 1 failure / 0 unknown, GPU_RELEASE_CONFIRMED / HAND_BACK. Do not execute remaining checkboxes or resume model selection under this plan. Current decision and evidence are in `docs/MODEL_SELECTION.md` and `reports/model-eval/20261006T051242Z/comparison.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not delegate implementation or contact another thread without authorization.

**Goal:** Compare existing 4B/27B models in 24 real, serial requests on the RTX 5070 Ti using only E-owned runtime state during an upstream-owner-established exclusive GPU window.

**Architecture:** Offline-copy verified model assets, run one project-owned Ollama instance on loopback 11535 (after availability check), supervise one resident candidate at a time, and produce an auditable result/cleanup report. The owner pauses/restores property-ai; this project only reads upstream assets and OS observations. State and resource guards are independent of upstream adapters/bindings.

**Tech Stack:** Existing Python 3.12, HTTPX, pytest, stdlib Windows process/file support, existing local Ollama binary and nvidia-smi. No dependency upgrades, model downloads or cloud services.

**Spec:** ../../INDEPENDENT_MODEL_EVAL.md. Read the spec and E:/enterprise-knowledge-base/AGENTS.md first.

## Global Constraints

- Project root is E:/enterprise-knowledge-base; all writes, model copies, temp/cache, lock/ledger/log/report paths stay inside it.
- D:/property-ai, including tmp and configuration, is strictly read-only. Never call 11435/11436/11437/11438 or any upstream guarded adapter.
- Upstream original thread/owner establishes and later restores the exclusive GPU window. Its confirmation is required before starting any project model service.
- Same RTX 5070 Ti, one E-owned service, one model resident and one request active. No second GPU/host/cloud requirement.
- Existing tags: qwen3.5:4b and qwen38-27b-q3km-g55-ctx8192:7af3b8f; verify actual source manifest/blob digests before copying. Copy no upstream config/log/runtime state.
- Fixed 12 cases x 2 = 24 reservations maximum, no automatic retry/replay or extra generation warmups. Any failure/unknown halts remaining calls and triggers owned-service shutdown.
- Equal generation settings: temperature 0, reasoning disabled, output cap 512; effective context target 4096 each, verify actual behavior, fail on mismatch. Record watchdog policy before sending per spec.
- Preserve free RAM/GPU floors 4500 MiB / cold 6000 MiB / running 2000 MiB in the E supervisor. Do not change upstream settings.
- After success or failure stop owned processes and verify own GPU release; only report. Never restore property-ai.

## Review Focus

- Stale/fabricated/revoked owner confirmation or unexplained GPU workload must not enable runtime start (Task 2).
- A model manifest can refer outside its model store, be missing/corrupt, or change during copying; destination must not become launchable (Task 1).
- Process IDs can be reused and a port can belong to another process; never terminate a non-owned identity (Task 3).
- Ctrl+C, process death, partial ledger write or timeout must not release for an unconfirmed request/replay (Tasks 3–4).
- Model settings, citations, absent metrics or GPU cleanup can look superficially valid; verify effective settings and distinguish unknown from zero/success (Tasks 3–5).

## Task 1: Offline model asset inventory and verified copy

**Files:** Create scripts/model_eval_assets.py; tests/test_model_eval_assets.py. Outputs runtime/model-eval/asset-inventory.json and runtime/models/.

**Interfaces:** `inventory_assets(source: Path, tags: tuple[str, ...]) -> dict`; `copy_verified_assets(inventory: dict, destination: Path) -> dict`. Inventory records public tag/manifest/blob digests, relative names and sizes; no credential/config values.

- [ ] Write tests for two tiny synthetic manifests sharing a blob, missing blob, wrong digest, path traversal/reparse escape and source mutation between inventory/copy. Assert no usable final destination on failure, source bytes unchanged, deduplicated closure on success.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_model_eval_assets.py -q`; observe expected RED.
- [ ] Implement bounded source read + destination-only staging, hash verification, disk-capacity check and atomic promotion within E. No recursive upstream tree copy, link, pull or service start.
- [ ] Run the same test command to GREEN, then perform the authorized local inventory/copy with a report. Report digest mismatches rather than guessing a replacement model.
- [ ] Preserve project-only WIP/commit according to the current delivery workflow; do not touch upstream Git.

## Task 2: Independent configuration and exclusive-window admission

**Files:** Create scripts/model_eval_window.py; tests/test_model_eval_window.py. Outputs runtime/model-eval/config.json and window.json.

**Interfaces:** `validate_owned_config(config: dict, root: Path) -> dict`; `verify_window(confirmation: dict | None, observations: dict) -> dict`. Consumes Task 1 inventory. Returns verified immutable window/config data or a stable refusal code; never starts a service.

- [ ] Write tests rejecting absent confirmation, historical idle evidence alone, a revoked/expired window, upstream endpoint/paths, busy selected port, foreign model workload and unknown process attribution. Accept authentic recorded owner confirmation + consistent observations without requiring a second GPU or zero desktop VRAM.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_model_eval_window.py -q`; observe RED.
- [ ] Implement explicit owner-message provenance recording and read-only OS/GPU observations. Confirmation may be relayed by the user. No HTTP calls to upstream, no owner-thread messages, no invented attestation or shared lock.
- [ ] Run GREEN. Keep current live status WAITING_UPSTREAM_EXCLUSIVE_WINDOW, calls 0, server NOT_STARTED. Synthetic tests do not establish a real window.

## Task 3: Owned service lifecycle, supervision and cleanup

**Files:** Create scripts/model_eval_runtime.py; tests/test_model_eval_runtime.py. Outputs runtime/model-eval/services/.

**Interfaces:** `start_owned_service(config: dict, window: dict) -> dict`; `observe_owned_service(receipt: dict) -> dict`; `stop_owned_service(receipt: dict) -> dict`. Consume Task 2 verified objects. Receipt binds PID/start time/executable/listener/process tree and E paths.

- [ ] Write mocked/no-model tests for missing window (no process spawned), correct isolated env/log directories, foreign/reused PID, occupied port, launch failure, interrupted cleanup, surviving child and unknown GPU counters. Assert no process termination without exact identity match and no false GPU_RELEASE_CONFIRMED.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_model_eval_runtime.py -q`; observe RED.
- [ ] Implement hidden process launch using an explicit child environment, loopback-only owned port, E model directory and all writable directories under E. Disable cloud/prune/download fallback; pin runtime/version and single residency/concurrency. Keep upstream environment/receipts untouched.
- [ ] Implement exact owned-tree shutdown, listener exit, process/GPU release checks and bounded failure reporting. Distinguish service health, completed inference, unloaded model and process exit.
- [ ] Run GREEN without launching a real model server. Service start remains prohibited until the actual owner window is confirmed.

## Task 4: Durable 24-call runner and interruption handling

**Files:** Create scripts/run_independent_model_eval.py; tests/test_independent_model_eval.py. Adapt scripts/prepare_model_selection.py only for new explicit E endpoint bindings; keep old reports/model-selection-manifest.json as history. Never re-enable scripts/model_window.py.

**Interfaces:** `prepare_owned_manifest(reference: dict, config: dict) -> dict`; `run_authorized_eval(manifest: dict, runtime: dict, window: dict) -> dict`. Consume prior task receipts; persist authorization binding, per-case reservation, in-flight marker and results under E. All transport/process/clock probes must be injectable for tests.

- [ ] Write tests with a fake local transport: 24 unique pairs only, prior reservation cannot replay, altered manifest refused, interrupted request cannot become COMPLETED, failure on case N sends no N+1, unknown survives restart, wrong model/digest/effective context refuses continuation, competing workload/window revocation stops own service, unload must finish before second candidate loads.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_independent_model_eval.py -q`; observe RED.
- [ ] Implement the state machine from the spec. Reserve/fsync before send; count failures/unknowns; use E-only markers; monitor resources/window and preserve raw outcomes. Disable retries and model auto-downloads. Endpoint must be the receipt-bound own service.
- [ ] Implement `finally`/interruption handling that stops only owned service, confirms or marks uncertain cleanup, and produces handback evidence. A crash/restart never resumes an unknown run automatically.
- [ ] Run GREEN and the existing suite; inspect required metadata and validate equal prompt/evidence/settings for all 12 pairs. No real calls or warmups while waiting for the owner.

## Task 5: Authorized real run, scoring and handback

**Files:** Create scripts/score_model_comparison.py; tests/test_model_comparison_scores.py; reports/model-eval/<run-id>/; update docs/MODEL_SELECTION.md and reports/VERIFICATION.md after actual results.

**Interfaces:** `summarize_scored_results(records: list[dict], rubric: dict) -> dict`. Consumes raw Task 4 outputs and explicit per-case reviewer scores; missing evidence remains null and failures remain in denominators.

- [ ] Test aggregation with synthetic records containing citation-ID-valid but unsupported claims, true refusals, unnecessary refusals, truncation, timeouts and missing resource data. Verify no faithfulness score is inferred from citation syntax and no latency/resource null becomes zero.
- [ ] Run RED, implement aggregation, run GREEN. Freeze rubric and case order before inference; no candidate self-judging.
- [ ] Read-only review code/plan consistency and perform full offline tests. Resolve execution-blocking findings before consuming the window.
- [ ] **Wait for actual upstream-owner confirmation.** Capture provenance and verify OS/GPU/port/resource evidence. If missing/contradictory, remain 0/24; never pause upstream services yourself.
- [ ] Start own service, run 4B then unload/verify then 27B; enforce the maximum 24 requests. First case per candidate measures cold load; no hidden generation warmup. Failure/unknown goes directly to owned cleanup and partial report.
- [ ] Stop own service after success or failure, verify owned processes/listener/GPU residency released, then publish a handback report stating exact completed/failed/unknown/not-attempted counts. No property-ai restoration actions or messages to others without explicit communication authorization.
- [ ] Score actual quality, faithfulness, citation correctness, multi-document reasoning and refusal against evidence; summarize cold/warm latency and resource observations, limitations and model-selection rationale. Preserve raw answers and reviewer reasoning.
- [ ] Record fixed 4B/fixed 27B/routing conclusion only if supported; otherwise state evidence insufficiency. No automatic extra calls for routing validation. Distinguish comparison completion from production acceptance.

## Current execution ledger

- [x] Updated the design and ownership boundary to the user's same-GPU exclusive-window instruction.
- [x] Retained the old shared-window runner in a revoked/fail-closed state.
- [ ] Implement/verify the above independent runner and offline-copy assets (not performed by this documentation update).
- [ ] Receive upstream-owner confirmation that the exclusive window is established.
- [ ] Launch own service and execute real requests (currently 0/24).
- [ ] Stop own service, confirm own GPU release, report/hand back; upstream owner restores property-ai.
