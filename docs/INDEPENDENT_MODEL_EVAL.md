# Independent 4B / 27B evaluation — exclusive window on the existing GPU

Status (2026-10-06): **17/24 real calls; fail-closed stop; GPU_RELEASE_CONFIRMED / HAND_BACK; no active model window.** Project-owned model service: **STOPPED**. The owner accepted this stopped run and ended model-selection expansion. See [per-case result](../reports/model-eval/20261006T051242Z/comparison.md) and [current operating decision](MODEL_SELECTION.md). The design and historical events below remain for provenance; they are not permission to resume the run.
Active project: **E:/enterprise-knowledge-base**.

The latest user instruction approves the same-machine, same-GPU approach below. A second GPU, second host or cloud resource is **not required and will not be sought**. This supersedes the earlier physical-isolation proposal. The remaining execution gate is the upstream owner explicitly establishing a verifiable exclusive GPU window, plus successful local preparation/preflight. Approval of this design is not evidence that the window has already been established.

## Ownership and non-negotiable boundaries

| Operation | Owner |
| --- | --- |
| Confirm no property-ai business/Worker/model tasks; pause its AI services and model occupancy without source/config changes | property-ai original thread/owner |
| Confirm 11435/11436/11437/11438-associated model processes no longer occupy the GPU; hold that state for the agreed window | property-ai original thread/owner |
| Read model asset manifests, verify public tag/digests, offline-copy only required existing weights/manifests into E | Codex C |
| Start, supervise and stop E-owned model server and its descendants; run up to 24 reserved requests | Codex C, only after window confirmation |
| Confirm own process exit/GPU release and report results or incomplete cleanup | Codex C |
| Restore property-ai services and verify their health | property-ai original thread/owner only |

D:/property-ai source, Git, configuration, tmp, locks, runtime bindings, ledgers, database and business data remain read-only for Codex C. No shared lock/barrier writes, upstream service controls, configuration copies, log copies or runtime-state copies. No HTTP calls to 11435/11436/11437/11438 or guarded adapters for this experiment, including health probes; use owner-provided evidence and local OS/GPU observations. No upstream credentials are needed.

All new project source, configuration, runtime state, locks, ledgers, data, logs, reports, model copies, caches and temporary files live under E:/enterprise-knowledge-base. Existing installed runtime binaries can be used read-only; service working directory, child environment and writable paths belong to E. No new model download, cloud call or cloud cost.

## Independent model assets and service

Copy only the existing manifests and blobs required by these candidates:

| Candidate | Reference native model tag |
| --- | --- |
| 4B | qwen3.5:4b |
| 27B | qwen38-27b-q3km-g55-ctx8192:7af3b8f |

Treat these as source references until a fresh local asset inventory verifies tag, manifest digest and every referenced blob digest. Inventory the exact dependency closure and free disk space before copying; verify source and destination integrity and promote only a complete verified copy. Never copy upstream configuration, adapter code, logs, receipts, runtime state or secrets. Never point the new service at the upstream model directory, use a junction, or share a writable model/cache directory. No pull command or missing-asset download fallback.

Use one E-owned Ollama service, with a free loopback-only project port (planned 11535, recheck before launch), to run both candidates serially. Never occupy or fall back to 11435–11438. Record executable/version, GPU identity, model digests, effective settings and process ownership. Same installed runtime version and driver/GPU for both candidates; only one model resident and one inference in flight.

Proposed paths:
- runtime/models/: verified model manifests/blobs, copied offline.
- runtime/model-eval/config.json: independent endpoint/settings and pinned candidate identities.
- runtime/model-eval/window.json: provenance of the explicit owner confirmation and window scope/lifecycle; not a fabricated self-approval.
- runtime/model-eval/model.lock: serializes E-project operations only; it does not establish the upstream exclusive window.
- runtime/model-eval/authorization.json and ledger.jsonl: durable 24-call budget, reservations and completion state.
- runtime/model-eval/services/: own process identity receipts, stdout/stderr, unload/exit evidence and cleanup state.
- .cache/temp/ and .cache/uv/: project subprocess temporary files and dependency cache.
- evaluation/: frozen synthetic questions, evidence and scoring rubric.
- reports/model-eval/<run-id>/: owned preflight, model transitions, raw outputs, scores, latency/resource samples, final cleanup and handback.

Model assets may be verified/copied and offline code/tests prepared before the window. **Do not start the E-owned model service, load models, warm up models or send inference until the owner confirms the window.** The old shared-window module remains revoked; it must not be revived as the new supervisor.

## Establishing and verifying the exclusive window

An explicit message from the upstream original thread/owner, or its confirmation relayed by the user, must say the exclusive window is established. Record its source, time and scope in E. A prior idle snapshot, elapsed time or this design approval does not substitute for that confirmation. Codex C does not ask the upstream APIs to establish or restore it.

The confirmation covers:
1. No executing business, Worker or model tasks that require the GPU.
2. Relevant property-ai AI services/model occupancy have been paused by the owner without source/config edits.
3. Model processes associated with 11435/11436/11437/11438 do not occupy the GPU.
4. The owner will keep the window exclusive until handback, or provides an explicit end time/revocation condition.

Immediately before launching its own server, Codex C checks OS listener/process identities and GPU compute-process/memory observations read-only. Confirm that observed state is consistent with the owner's evidence, that 11535 is free, and that no unknown competing model/compute process is present. Record normal desktop/display GPU baseline separately; do not demand zero total VRAM use. If driver/WDDM observations cannot establish process ownership, reconcile with owner process-exit evidence; do not silently interpret unavailable counters as zero.

If evidence is stale, contradictory, incomplete, the window is revoked/expires, or competing model activity appears, do not start or continue inference. Codex C does not stop another project's process. Its own active request goes through bounded shutdown/unknown handling; later requests remain stopped. An E-only lock and resource polling supplement the window, not replace it.

## Execution and failure control

State sequence: PREPARED -> WAITING_UPSTREAM_WINDOW -> WINDOW_VERIFIED -> OWN_SERVICE_READY -> RUNNING_4B -> OWN_MODEL_UNLOADED -> RUNNING_27B -> OWN_SERVICE_STOPPED -> GPU_RELEASE_CONFIRMED -> HANDBACK_REPORTED. Failure/unknown/interrupt enters STOPPING_OWN_SERVICE and prevents all later calls/switches.

- Supervise only exact project-owned identities: PID, executable, start time, parent/child relationship and listener. No kill-by-name or port-only kill.
- Retain the starting resource floors: at least 4500 MiB free RAM, 6000 MiB GPU free before cold loading, and 2000 MiB GPU free during inference. They are E-supervisor admission/abort rules, not changes to upstream thresholds. Validate observations and record sampling gaps.
- Pin generation settings equally: temperature 0, reasoning disabled, 512 output tokens; target effective context 4096 for both through supported explicit runtime/request settings, verified in actual residency metadata. Preserve model weights/tag/digest. The 27B tag name contains ctx8192, so never assume a tag suffix proves effective context. If a common effective context cannot be established, stop and record the limitation instead of silently comparing mismatched settings.
- Use one fixed watchdog policy for both candidates, recorded before sending (load cap 120s, generation cap 180s, bounded total request cap 300s). If separate phases cannot be reliably observed, enforce the stricter single 180s request cap and label load-inclusive latency; do not improvise extensions after seeing an output. Deadline must also fit the owner's remaining window.
- Reserve each candidate/case before send; failed/unknown attempts consume the budget. Use a durable E-only in-flight marker so process death cannot cause automatic replay. No retries, speculative calls or uncounted generation warmups.
- On normal candidate transition, unload only the E-owned model and verify it is absent and resources have returned to an acceptable baseline before loading the other. Verify loaded tag/digest/effective context. Never unload or control upstream models.
- On load failure, health failure of own service, resource violation, lost exclusivity, interruption or unknown completion: stop all later requests/switches; stop the owned service/process tree, verify listener closure and owned GPU process/residency release. Preserve evidence, including incomplete results.
- After either 24 calls or early failure/unknown, always stop the project-owned server. No automatic restart/continuation of an aborted run. If cleanup cannot be confirmed, report CLEANUP_UNCONFIRMED, retain the E barrier and do not claim GPU release or invite upstream restoration into uncertain occupancy.
- Final output is a result/handback report. Codex C never restores property-ai, verifies its business health through live APIs, or writes a restoration binding for it. The original thread/owner performs restoration and its own health checks.

For every model transition record before-state, prior unload/stop evidence, candidate load result, evaluation completion/failure and own-service cleanup. The previous requirement to restore upstream services by Codex C is superseded by the user's ownership split.

## Twenty-four calls and measurements

The original plan was 12 synthetic cases x 2 candidates on the same RTX 5070 Ti and E-owned service. It stopped at 17/24; seven 27B cases were never sent. The frozen case/evidence/prompt hashes and execution manifest are preserved in the E report. The old manifest with upstream URLs remains historical PREPARED_NOT_RUN material and is not executable authorization.

Run all 4B cases, verify unload, then all 27B cases; declare this order before inference. First request in each group is cold/load-inclusive, later calls are classified from actual residency. Preserve wall time and runtime timing if available. Single order/one sample per pair can confound thermal/background effects; do not present these 24 calls as a statistically conclusive performance study.

Report per case and per candidate:
- answer quality and required fact coverage;
- faithfulness of individual claims to the supplied passages;
- citation correctness/entailment, separately from ID validity;
- complex multi-document completeness;
- correct refusal, including unsupported/private-evidence and injection cases;
- latency, cold/warm status, truncation/timeout/failure;
- RAM/VRAM baseline, minimum free/peak used, observed utilization and sample gaps; distinguish device-level observations from process-attributed usage.

Use gold evidence and an explicit reviewer rubric; do not use a candidate to grade itself. This experiment compared generation with fixed evidence, not end-to-end retrieval. Existing offline retrieval scores remain separate. The accepted local 4B default is an operating choice, not a 4B quality win. Routing was not implemented. No additional calls or holdout are authorized in the current closeout.

## Current stop point and zero-inference recovery

The 2026-10-05 exclusive window ended after an observation failure during owned-service startup. No generation request was sent: 0/24. The upstream owner restored its own services after E-side release was independently confirmed. That confirmation is historical and cannot authorize another run.

The startup observation layer now preserves the failing command and exit code; startup and cleanup errors remain separate. The original trace discarded the command name, so the exact command that failed in that historical attempt cannot be reconstructed. A zero-model E-owned fixture verified PID, listener, executable/start-time receipt, stdout/stderr capture, an exception unwinding through cleanup, a root crash, and cleanup of a child created after readiness. The final 0-inference drill is in `runtime/dry-run/20261005T162547Z/report.json`. Its model-residency response was empty, both owned listeners closed, all observed owned children exited, GPU process IDs returned to baseline, and neither log contained a generation request. The old unverified receipt was archived only after matching the prior release proof and fresh OS PID/port/GPU checks; see `reports/model-eval/legacy-receipt-retirement.json`.

The production runner requires `--window-confirmation <E-owned JSON path>` with a new, time-bounded owner confirmation. The old hardcoded 2026-10-05 confirmation cannot be reused. Its preflight also requires repeated low-utilization, high-free-memory GPU observations before establishing a new baseline. At this earlier stop point, the offline runner suite passed 59 tests and an independent read-only review found no unresolved P0/P1. The status was **READY_FOR_NEW_GPU_WINDOW** for a future owner-confirmed attempt, not permission to start one without confirmation.

## 2026-10-06 renewed window and second zero-call stop

The owner explicitly reconfirmed the same physical window at 11:50:53 Asia/Shanghai, while preserving its original 00:55:26 start. The runner now keeps both timestamps and only accepts a renewal with the exact owner-confirmation source, continued-hold flag, and a confirmation age of at most one hour. Local preflight passed with the original ports and 11535 closed, no foreign model process, and three quiet GPU samples above 12,000 MiB free.

The E-owned Ollama process began listening on 11535 at 11:53:14.846 but did not finish GPU discovery until 11:53:19.399. The first `/api/version` read timed out during that interval. The runner stopped before any generation request: **0/24, success 0, failure 0, unknown 0**. Its synchronous cleanup status was `CLEANUP_UNCONFIRMED` because startup had not yet produced a verified process-tree receipt. An independent OS/GPU check shortly afterward confirmed the exact root PID and descendants absent, 11535 closed, no model process, and no new GPU PID; see `reports/model-eval/20261006T035312Z/post-abort-release-verification.json`. HAND_BACK was reported. Only the upstream owner restores property-ai. The failed unverified E receipt was archived after matching that proof and current OS absence; see `reports/model-eval/20261006T035312Z/receipt-retirement.json`.

The runner now gives its single idempotent version GET the remaining portion of the original 20-second startup deadline, allowing Ollama's GPU discovery to finish without a premature three-second read timeout. A real timeout is terminal: no version or generation request is retried afterward. Process identity checks and stop-on-failure behavior remain in force. Unit tests cover later readiness and immediate stop/cleanup on a read timeout. The final E-owned zero-model drill `runtime/dry-run/20261006T040131Z/report.json` exercised delayed version readiness, root crash with a later child, and interruption unwind. All three cleaned up, logs showed zero POSTs and empty model residency, and GPU PIDs returned to baseline. The full offline suite passes 63 tests and Ruff passes. **No active exclusive window remains for real inference; a future attempt needs a fresh owner confirmation and local preflight.**

An independent review found that an already handed-back confirmation could still be replayed within its one-hour freshness interval. The runner now consumes both an E-owned `window_id` and a fingerprint of the owner event (source plus establishment/reconfirmation times) before preflight or service start. Changing only a local window label cannot replay the same owner event. The 2026-10-06 ID and event fingerprint were retrospectively marked consumed only after its exact run confirmation, zero-call summary, and release proof matched. A future run needs a new owner-issued window event and ID. This guard does not change the fixed 24-call case budget.

## 2026-10-06 readiness repair after handback

The second window is closed; this repair and its validation do not establish a new exclusive GPU window or authorize inference. `runtime/model-eval/config.json` now sets `startup_readiness_timeout_seconds` to 60 and `startup_connect_timeout_seconds` to 2. The runner validates the policy before launch and rejects values above 60 for this round. The deadline starts at process launch. A listener is recorded as an intermediate state; only one `/api/version` GET is sent, with a short connect timeout and read timeout equal to the remaining startup window. There is no version retry. Version 0.34.4, a live root process, elapsed time below the deadline, and a complete GPU-probe timing pair from Ollama stderr are required before the receipt becomes READY. The generation entry rejects any receipt that is not READY.

The E-only receipt records launch request and OS process start times, listener time, version request start/end and duration, GPU discovery start/end and wait, stdout/stderr paths, READY or FAILED status, and timeout/exit reason. A separate monotonic wall-clock watchdog bounds the entire single version GET, even if the server slowly streams bytes before each network read timeout. At a watchdog cutoff, the receipt records the wait cutoff separately; it records the actual request end only after the GET worker exits. The process tree is captured and persisted before the potentially long version GET so root death or interruption during GPU discovery still enters owned-tree cleanup. Final deadline checks follow log parsing, tree observation, and receipt persistence; no late READY result is returned. Cleanup failure or an unconfirmed GET worker remains fatal and cannot be reported as readiness. If actual Ollama is still not READY within 60 seconds in a future independently authorized window, stop and inspect that run's Ollama logs; do not raise the cap on the basis of the timeout alone.

Final offline verification passed 73 tests and Ruff check/format. The final-code, no-model drill is `runtime/dry-run/20261006T043001Z/report.json`: delayed readiness completed in 24.422 seconds, GPU-probe fixture wait was 23.353 seconds, root-crash and interrupt paths released their owned process trees, all three runs had empty model residency and zero generation POSTs, and post-drill GPU process IDs matched baseline. Independent read-only review found no unresolved P0/P1. This does not validate actual Ollama readiness or reopen the handed-back GPU window.

## 2026-10-06 new window: accepted 17-call stop

Owner window `20261006-1302-ekb-window` passed fresh E/OS/GPU preflight. The independent Ollama reached version READY in 4.281 seconds, then the runner sent 12 serial 4B requests, verified unload, and sent five 27B requests. The 27B M2 answer covered its five expected facts and pointed to supporting passages but serialized both citation IDs as strings; the frozen integer-ID contract marked it `FAILURE`, so the runner stopped at **17 calls: 16 success / 1 failure / 0 unknown**. There was no M2 replay, extra warm-up generation or attempt at the seven remaining 27B cases. The E service and children stopped, 11535 closed, and an independent immediate OS/GPU check confirmed no new GPU PID before HAND_BACK. The owner accepted this stop. See `reports/model-eval/20261006T051242Z/summary.json`, `comparison.md` and `post-run-release-verification.json`.

The current policy is 4B as the local 16 GB model-enabled default and 27B as a configurable high-quality experimental candidate. 27B's observed complex-case completeness advantage and its schema, latency and VRAM constraints are retained; 4B's S3/M4 omissions and M1/B1 false refusals are retained. Neither candidate is declared a general quality winner. No new GPU window, routing, holdout or continuation of this plan is requested; these are Future Work only if final portfolio truthfulness makes further evidence necessary.
