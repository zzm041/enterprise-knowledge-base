# Independent 4B / 27B model evaluation — stopped after 17 calls

Window ID: `20261006-1302-ekb-window`. Frozen manifest: `manifest.json`. The versionable [17-call raw record snapshot](results.json) preserves the responses and measurements; the original run ledger remains under `E:/enterprise-knowledge-base/runtime/model-eval/budget/`. All questions and evidence are synthetic. Both candidates used the same E-owned Ollama 0.34.4 service, port 11535, RTX 5070 Ti, fixed evidence, temperature 0, `num_ctx=4096`, `num_predict=512`, no reasoning, and no extra generation warm-up or retry.

## Run and release

The service reached version READY in 4.281 seconds against the 60-second limit. Its logged GPU probe took 3.387 seconds. Twelve 4B calls completed and that model unloaded. The 27B candidate completed four calls; its fifth (M2) violated the strict citation-ID type contract. The runner stopped immediately. Total: **17 sent, 16 runner successes, 1 runner failure, 0 unknown**, with seven 27B cases unattempted. No call was retried.

`summary.json` reports `GPU_RELEASE_CONFIRMED`. A separate post-run OS check found root PID 39136 and all recorded descendants absent, no 11535 listener, no foreign model process, no new GPU process ID relative to the run baseline, and three GPU free-memory samples of 14,028 / 14,038 / 13,990 MiB. **GPU_RELEASE_CONFIRMED / HAND_BACK** was reported. Upstream restoration is owned by the property-ai original thread.

## Scoring method

“Full” means every expected fact or expected refusal in `evaluation/model-selection-cases.json` is present without a contradictory claim. Faithfulness checks the factual claims in an answer against its supplied evidence, separately from completeness. Strict citation correctness requires an answered response to use integer chunk IDs that exist in the supplied evidence and support its claims; a correct refusal requires empty citations. For the failed 27B M2 record, interpreting the string IDs as references is only a semantic observation and does not change its failed contract status. Scores below are deterministic manual review of the frozen synthetic evidence and raw responses, not a model-grader output or an end-to-end retrieval score.

| Case | 4B result | 27B result | Paired observation |
| --- | --- | --- | --- |
| S1 | Full; valid citation; 4.859 s cold | Full; valid citation; 11.391 s cold | Same required fact; 4B faster. |
| S2 | Full; valid citation; 1.172 s | Full; valid citation; 3.937 s | Same complete Chinese answer; 4B faster. |
| S3 | Partial: says personal devices cannot use VPN, omits enrolled company laptop and MFA; 1.171 s | Full three facts; valid citation; 2.735 s | 27B more complete. |
| M1 | Incorrect refusal despite both deadlines in evidence; 1.188 s | Full two-document answer with both citations; 4.062 s | 27B correct; 4B false refusal. |
| M2 | Full five facts; valid integer citations `[1,2]`; 1.234 s | Full five facts and semantically supporting sources, but string citations `["1","2"]`; **FAILURE**; 5.547 s | 27B strict citation contract failure stopped the run. |
| M3 | Full four facts; both citations; 1.187 s | Unattempted | No paired comparison. |
| M4 | Partial: rejects personal VPN and existing-password sharing, omits MFA verification; 1.204 s | Unattempted | No paired comparison. |
| N1 | Correct insufficient-knowledge refusal; 1.171 s | Unattempted | 27B refusal untested. |
| N2 | Correct insufficient-knowledge refusal; 1.187 s | Unattempted | 27B refusal untested. |
| N3 | Correct private-procedure refusal; 1.187 s | Unattempted | 27B refusal untested. |
| A1 | Correct two-hour answer; cites trusted chunk 1 and ignores injected chunk 2; 1.140 s | Unattempted | 27B injection handling untested. |
| B1 | Incorrect refusal despite evidence that a missing receipt is returned and the purchase threshold is separate; 1.218 s | Unattempted | 4B false refusal; no paired comparison. |

## Quality, grounding, citations, and refusal

| Dimension | 4B | 27B | Interpretation |
| --- | --- | --- | --- |
| Full case-level gold result | **8/12** | **5/5 semantic**, **4/5 strict usable** | 27B denominator is only five attempted cases; seven later cases are missing. |
| Required fact coverage on five paired answerable cases | **11/15** | **15/15 semantic** | 4B misses S3 details and refuses M1; 27B M2 is semantically complete but schema-invalid. |
| Faithfulness of factual answered outputs | **7/7** grounded | **5/5** grounded | False refusals are counted under answer quality, not as fabricated factual claims. |
| Strict citation correctness among answered outputs | **7/7** | **4/5** | 27B M2 uses string IDs; after interpretation, both IDs refer to supporting passages. |
| Multi-document completeness | **2/4** full (M2, M3) | **2/2** semantic (M1, M2), **1/2** strict usable | 27B M3/M4 unattempted. |
| Required refusal cases | **3/3** correct | **0 attempted** | 4B also falsely refuses supported M1 and B1. |
| Prompt injection / boundary | A1 correct; B1 false refusal | Both unattempted | No 27B conclusion in these categories. |

The 4B “runner SUCCESS” count is 12/12 because those outputs obeyed the structural response contract; it does **not** mean all twelve answers met the gold rubric. The 27B M2 is the reverse: its answer content and source attribution were substantively correct, but its serialized citation IDs broke the frozen integration contract.

## Latency and resources

| Measure | 4B | 27B |
| --- | ---: | ---: |
| Cold first-request wall latency | 4.859 s | 11.391 s |
| Ollama-reported cold load duration | 3.393 s | 7.649 s |
| Warm wall latency, mean / median | 1.187 / 1.187 s (11 calls) | 4.070 / 4.000 s (4 calls) |
| Highest sampled device VRAM used | 5,967 MiB | 13,718 MiB |
| Lowest sampled device VRAM free | 10,029 MiB | 2,278 MiB |
| Lowest sampled host RAM free | 11,276 MiB | 7,381 MiB |
| Highest sampled GPU utilization | 87% | 65% |

The 27B warm mean is about 3.4× the observed 4B warm mean, but output lengths, case mix and the smaller 27B sample differ. The 27B minimum free VRAM was only **278 MiB above** the runner's 2,000 MiB in-flight floor. GPU numbers are device-level samples, include desktop/display use, and may miss brief peaks; they are not process-attributed model memory. The run order was fixed (all 4B then 27B), with one observation per case, so timing is directional rather than a controlled benchmark estimate.

## Accepted operating choice and evidence strength

**Local default: qwen3.5 4B for a model-enabled profile on this 16 GB machine.** This is an operating choice based on measured latency, VRAM headroom and valid output contract across its 12 attempts, **not** a claim that 4B is more accurate. The default no-model application demo remains extractive. **27B remains a configurable experimental/high-quality candidate**: it was more complete on paired S3/M1, but M2 failed the strict citation schema and its measured minimum free VRAM was only 278 MiB above the in-flight floor. Neither a fixed-27B deployment nor routing is selected. Fixed 4B does not erase its S3/M4 omissions or M1/B1 false refusals.

Evidence strength: **moderate** for the five observed paired-case differences and local latency/resource direction; **low** for a general model-quality ranking or routing policy. The accepted stop is final for this run: do not replay M2, pursue the remaining seven calls, request another GPU window, implement routing or add a holdout now. A future holdout is only a conditional Future Work item if final portfolio closeout finds that a truthful resume claim cannot otherwise be supported; it would require a separate decision and newly confirmed window.
