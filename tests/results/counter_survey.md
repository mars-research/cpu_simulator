# Counter survey: load-uop memory behaviour vs the uiCA memory model

d760 (Xeon Gold 6548Y+, EMR / Raptor Cove), 2.5 GHz, HW prefetchers off, 1 GiB pages, cpu 0, 2026-11-04.
Script: `survey_counters.py` (reuses `measure.py`; each group = cycles + instructions + <= 4 programmable,
reps=1, only the timed loop counted). Raw data: `results/counter_survey.json` (event -> test ->
per_iter / per_cycle / group), logs `results/counter_survey.log`, `results/counter_survey_extra.log`.
`osr_data_rd_geN` = cycles with >= N outstanding L2-miss data reads (OFFCORE_REQUESTS_OUTSTANDING.DATA_RD
with cmask N), `osr_demand_rd_geN` the same for demand reads only, `l1d_pend_geN` = cycles with >= N
demand-held fill buffers (L1D_PEND_MISS.PENDING, cmask N). `_ldlat` = PEBS load-latency samples
(MEM_TRANS_RETIRED.LOAD_LATENCY, ldlat=4, period 23): time from first dispatch to completion per instruction.

## Event availability and counter restrictions (Intel perfmon EMR v1.25 + perf 6.8)

- HT is on (cpu 0 / 64 siblings): 4 programmable counters per thread; most memory events (ld_blocks,
  l1d_pend_miss, memory_activity, l2_rqsts, offcore_*, cycle_activity, ...) are restricted to counters 0-3
  anyway. `topdown.bad_spec_slots` / `br_mispredict_slots` / `mem_trans_retired.store_sample`: counter 0
  only. `mem_trans_retired.load_latency_*`: counters 1-7, PEBS + an auxiliary `mem-loads-aux` event, so it
  is only usable by sampling (`perf record`), not `perf stat`. OCR (`ocr.*`) events share the two
  OFFCORE_RSP MSRs: 3 OCR events in one group multiplexed (49 %), so the script keeps <= 2 per group.
  `l2_lines_out.*`, `l2_rqsts.*`, `xq.full_cycles` are core-scoped ("non-threaded"); the sibling cpu 64
  was idle.
- Not available on this core: **no `ld_head.*`** (not in perf's or Intel's EMR event list; it exists on
  later cores), no load-buffer-full / ROB-full / RS-full resource_stalls events (only `resource_stalls.sb`
  and `.scoreboard`), no event for "software prefetch dropped". `exe_activity.bound_on_loads`,
  `memory_activity.*`, `rs.empty` are the closest substitutes.
- No group multiplexed or was rejected in the final runs (`_notes` empty).
- Caveat: mix_ld_t2x2_dram ran at 36.8 cyc/iter in the main run (= ground_truth.json) but at 33.5-33.7 in
  the later extra run and a re-time; all other tests reproduced within 1 %. Ratios below use per_cycle
  values from the same run.

## Main findings (evidence first, then interpretation)

### 1. An L1 miss that finds the fill buffers full is sent to L2 at once as an "L2 hardware prefetch" (the FB-full L2 hint) — this is the dominant miss path

Evidence (HW prefetchers are off, MSR 0x1a4 = 0x2f, so no real HW prefetch exists):

| test | fb_full / cycle | `l2_rqsts.hwpf_miss` / `l2_rqsts.miss` | `ocr.hwpf_l2` | `ocr.hwpf_l1d` | demand true misses (`l2_rqsts.demand_data_rd_miss`) |
|---|---:|---:|---:|---:|---:|
| chase_dram | 0.00 | 0.00 | 0 | 0 | 1.00 |
| rand_dram | 0.70 | 0.64 | 0.14 | 0 | 0.37 of 1.0 |
| pf_t0_dram | 0.90 | 0.78 | 0.09 | 0.69 | 0 |
| pf_t2_dram | 0.68 | 0.57 | 0.56 | 0 | 0 |
| mix_t2x2 | 0.96 | 0.72 | 1.27 | 0.02 | 0 |
| mix_t0_t2x1 | 0.95 | 0.85 | 0.98 | 0.70 | 0 |
| mix_ld_t2x1 | 0.65 | 0.65 | 0.74 | 0 | 0.37 |
| mix_ld_t0x1 | 0.80 | 0.73 | 0.45 | 0.54 | 0.28 |
| mchase16 / 32 / 48 | 0.64 / 0.86 / 0.84 | 0.44 / 0.72 / 0.72 | 0.3 / 6.0 / 11.3 | 0 | 8.9 / 9.0 / 13.3 |

- The fraction of L2 misses that arrive as "HW prefetch" tracks the fraction of cycles with FB full in
  every test; chase_dram (FB never full) has none. `l2_rqsts.swpf_miss` is documented as counting software
  prefetches "when FB is not full" — consistent: in pf_t0 only 0.22 of 1 prefetcht0 is a swpf_miss, the
  other 0.78 is a hwpf_miss.
- The hint type depends on the destination: prefetcht0 hints go offcore as `hwpf_l1d` (0.69 in pf_t0),
  prefetcht2 and demand-load hints as `hwpf_l2`. For demand loads, most hinted requests are later
  "upgraded": rand_dram has 0.64 hwpf L2 misses but only 0.14 `ocr.hwpf_l2` and 0.86
  `ocr.demand_data_rd` per load, i.e. the load got its FB and caught up with its own hint before it left
  the core (L2 counts it as a merged, non-true miss).
- Hinted loads that did *not* catch up retire as **L2 hits with DRAM-plus latency**: `mem_load_retired.l2_hit`
  = 0.11 per load in rand_dram (PEBS: 345 cycles mean, vs 326 for "RAM hit"), 5.5 of 32 in mchase32 and
  10.2 of 48 in mchase48 (PEBS 370-550 cycles). Those loads look up L2 twice: L2 references exceed L1
  misses + software prefetches by 5.6 (mchase32) and 10.4 (mchase48) per iteration.
- Blocked loads/prefetches are **not re-dispatched** while they wait for an FB: port 2/3/10 dispatches per
  retired load are 1.00-1.02 in rand_dram, pf_*, every mix and mchase48 although FBs are full 65-96 % of
  cycles. In prefetch-only mixes the RS is empty 60-65 % of cycles (`rs.empty`) while 72-73 % of cycles
  are stalled: the waiting prefetches sit outside the RS (load buffer / MOB) and block retirement.
  (The extra dispatches in chase_dram, 1.86 per load, and mchase16/32, 1.45/1.37, are the usual cancelled
  speculative dispatch of a load whose address comes from a missing load, not FB replays.)

Model: `fbFullL2Hint` implements exactly this path but is **off** in the preset; instead per-type FB
service times (ld 244, t0 172, t2 122) are fitted. The counters say the hint path is real and carries
44-85 % of all L2 misses, so the fitted service times are averages over two very different paths (FB held
for the whole trip vs. FB taken late and held only until the data arrives / not at all for t2).

### 2. FB hold times per path (explains pf_t0 / pf_t2 being too fast in the model)

- pf_t0_dram (model 11.1 vs 13.21 measured): PEBS: prefetcht0 dispatch -> completion = **150 cycles mean**
  (p50 150, p90 268), i.e. the prefetch waits ~150 cycles for an FB (fb_full 90 % of cycles); the later
  load is an L1 hit (5 cycles) in 68 % of samples and an "LFB hit" averaging **152 cycles** in 32 %
  (`mem_load_retired.fb_hit`/`load_hit_prefetch.swpf` only 0.07 per iteration, so the PEBS LFB class
  likely also includes loads that waited for an FB; treat 7-32 % as the range). FB budget: 16 FBs x 13.2
  cycles = 211 FB-cycles per iteration; the 22 % non-hinted t0 holding an FB for the full ~330-cycle trip
  account for ~73, leaving ~177 cycles per hinted t0 = (316 DRAM - ~150 wait) + L2->L1: **a hinted t0
  takes its FB about 150 cycles late and then holds it until the data arrives**. Because the prefetch is
  effectively issued ~150 cycles late, with a distance of 32 x 13.2 = 422 cycles the data is often not
  back when the load comes (load waits up to ~300 cycles, p90 298).
- pf_t2_dram (model 8.9 vs 11.43): the demand load is an L2 hit (0.98/iter) but PEBS gives **94 cycles
  mean, median 51, p90 227** instead of 16: the L2 -> L1 refill needs an FB, and FBs are full 68 % of
  cycles. The FB-blocked load also sends its own hint to L2 (`l2_rqsts.all_hwpf` 1.18 = 0.57 misses
  from prefetcht2 + 0.61 hits from loads). Demand-held FB occupancy is only 1.1, so most FB time goes to
  prefetcht2: 16 x 11.45 = 183 FB-cycles per iteration minus the refill (~16-50) ~ 140-165 per t2, vs
  the fitted 122. Consistent with: the 43 % of t2 that get an FB hold it until the line is in L2
  (~316 cycles = ~136 per t2 on average), the 57 % hinted ones hold none. Prefetcht2 itself completes
  after 52 cycles mean (PEBS) in pf_t2.
- Mixes: prefetch completion latency (PEBS, = FB wait) is 96 (t2 in ld_t2), 92-100 (ld_t2x2), 141-146
  (t2x2), t2 47 / t0 216 (t0_t2), t0 206 (ld_t0): **t0 waits much longer than t2**, i.e. a waiting
  prefetcht0 cannot complete until it holds an FB, while prefetcht2 can complete once its hint is sent.
  These per-type waits are directly comparable with the model's fbWait statistics.

### 3. Outstanding-L2-miss distribution: two different limits (demand vs prefetch)

Mean outstanding data reads (`offcore_requests_outstanding.data_rd` / cycle) and fraction of cycles at or
above N:

| test | mean | demand mean | >=16 | >=24 | >=32 | >=40 | >=44 | xq.full | l1d_pend_miss.l2_stalls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rand_dram | 20.1 | 12.3 | 0.68 | 0.40 | 0.13 | 0 | 0 | 0.00 | 0.001 |
| pf_t0_dram | 22.9 | 0 | 0.91 | 0.49 | 0.03 | 0 | 0 | 0.00 | 0.001 |
| pf_t2_dram | 27.2 | 0.3 | 0.92 | 0.73 | 0.31 | 0.03 | 0.004 | 0.09 | 0.005 |
| mix_t2x2 | 37.1 | 0 | 0.99 | 0.99 | 0.98 | 0.19 | 0.006 | **0.91** | **0.19** |
| mix_t0_t2x1 | 36.4 | 0 | 0.99 | 0.99 | 0.94 | 0.16 | 0.011 | **0.85** | 0.02 |
| mix_ld_t2x1 | 25.3 | 8.5 | 0.81 | 0.59 | 0.35 | 0.04 | 0 | 0.03 | 0.001 |
| mix_ld_t0x1 | 24.7 | 6.2 | 0.80 | 0.57 | 0.31 | 0.03 | 0 | 0.01 | 0.002 |
| mchase32 | 22.7 | 13.0 | 0.87 | 0.55 | 0.02 | 0 | 0 | 0.02 | 0.002 |
| mchase48 | 24.2 | 12.7 | 0.83 | 0.59 | 0.23 | 0.002 | 0 | 0.09 | 0.003 |

- The hard per-core ceiling on outstanding L2 misses is **~44-47, not 48**: >= 44 occurs in <= 1.1 % of
  cycles and >= 48 never, and prefetch-only mixes sit at 32-43 nearly all the time with **XQ full 85-91 %**
  of cycles (`xq.full_cycles`: "the uncore cannot take any further requests"). So prefetch-only mixes
  are limited by the uncore request queue, not by FBs (FBs are also full 95 %, but demand FB occupancy is
  0 — the FBs are held by prefetches waiting on the XQ). Model: nL2Q = 48 with no back-pressure from
  the uncore; an XQ-like limit around 40-44 fits better.
- Demand reads never exceed 23 outstanding (`osr_demand_rd_ge24` = 0 everywhere) and demand-held FBs
  never exceed 16 (`l1d_pend_ge16` up to 0.35 of cycles in rand_dram, 0.30 in mchase48). The extra
  outstanding misses beyond 16 in rand/mchase are hints (rand: 20.1 total - 12.3 demand = 7.8 hinted).
- Mixes containing demand loads hold ~25 because loads keep their FB for most of the trip:
  `l1d_pend_miss.pending` per L1-miss load = 198 (rand), 219 (ld_t2), 244 (ld_t2x2), 167 (ld_t0),
  175-278 (mchase) cycles, vs. 135 (pf_t0, demand time on prefetch FBs) and ~12 (pf_t2 refill). FB-cycles
  per iteration (16 x cycles) are the binding budget in these tests (fb_full 62-86 %, XQ never full).
- mix_t2x2 dropped prefetches: L2 references 1.786 = L2 misses 1.785 = offcore reads 1.784 for 2.000
  executed prefetcht2 (`sw_prefetch_access.any` 2.000), so **0.21 prefetcht2 per iteration (11 %) never
  reach L2** — neither as swpf (0.49) nor as hint (1.29). Only this test has `l1d_pend_miss.l2_stalls`
  significant (19 % of cycles: "demand request waited ... due to lack of L2 resources"); mix_t0_t2x1
  (XQ full 85 %, l2_stalls 1.7 %) loses only 0.026, ld_t2x2 0.018. Interpretation (speculative but
  well-supported): a prefetcht2 that can get neither an FB nor an L2 request slot is dropped instead of
  waiting. Nothing in memsys drops prefetches.

### 4. Things that are measurably *not* happening (rules out candidate mechanisms)

- TLB: 1 GiB pages work — `dtlb_load_misses.walk_completed` <= 0.002, `stlb_miss_loads` 0.
- Store forwarding / memory disambiguation in mchase (store + reload of the chain pointer):
  `ld_blocks.store_forward` <= 0.008 per iteration, `machine_clears.memory_ordering` 0,
  `ld_blocks.address_alias` <= 0.003. `resource_stalls.sb` <= 0.3 cycles and `exe_activity.bound_on_stores`
  <= 0.19 per iteration even in mchase48 (48 stores/iteration): the store buffer is not a limiter here.
- `ld_blocks.no_sr` (split loads) 0, `l2_rqsts.all_rfo` 0, `l2_lines_out.silent` ~0 (clean evictions are
  non-silent: `l2_lines_out.non_silent` = 1 per L2 fill — LLC is non-inclusive, victims go to LLC, as
  modelled).
- Bad speculation / clears: `topdown.bad_spec_slots` < 0.1 %, `int_misc.recovery_cycles` ~0.
- `l2_lines_out.useless_hwpf` (0.66-1.27 per iteration in the t2 mixes) only says prefetcht2 lines (via
  the hint path) are evicted from L2 unused, as designed by the tests.

### 5. mchase32/40/48 too slow in the model (+14..19 %)

Measured: 22.7 (mc32) / 24.2 (mc48) L2 misses outstanding although only 16 FBs and demand <= 23;
72 % of the chase misses go through the hint path; 17-21 % of chase loads complete as late "L2 hits"
(PEBS 370-550 cycles). With `fbFullL2Hint` off, the model can only overlap misses via the fitted ld FB
service time (244 cycles < 316 trip), which caps throughput at 16/244 per cycle -> too slow; the hardware
instead issues the miss at dispatch and spends the FB only on the second half of the trip
(FB hold per L1 miss: 188 cycles in mc32, 175 in mc48, vs 278 in mc16 where the FB is full only 64 %).
Load latency spread is wide in mchase48 (PEBS p90 up to 600-950 cycles for some chains), which interacts
with the 512-entry ROB (one iteration = 215 uops, so only ~2.4 iterations fit) — not modelled as latency
variance (DRAMLat is a constant 316).

## Ranked list: what to add to the simulator

1. **Turn the FB-full L2 hint into the default path, per request type, and drop the fitted per-type service
   times**: a miss that finds all FBs busy issues its L2/uncore request at dispatch (counts show 44-85 %
   of all L2 misses take this path, matching fb_full); the uop then waits (outside the RS, no
   re-dispatch, blocking retirement) for an FB, which it holds until the data reaches L1. Requests that
   get an FB at once hold it for the whole trip. Prefetcht2 completes once its hint is sent (PEBS: t2
   waits 47-146, t0 150-216) and needs no FB for a hinted fill; prefetcht0 completes only when it owns an
   FB. Directly addresses pf_t0 (late t0, FB hits of 150 cycles), pf_t2, mchase32-48, rand_dram.
2. **L2 -> L1 refills (L2 hits, including lines brought by prefetcht2) need an FB and queue for it**: pf_t2
   demand loads take 94 cycles mean (p90 227), not 16. Check that memsys charges an FB wait for L2 hits
   when the FB is full and lets the load also hint (L2 counts 0.61 hwpf hits/iter).
3. **Uncore request-queue (XQ) limit ~40-44 with back-pressure into L1** instead of nL2Q = 48: prefetch-only
   mixes saturate at 37 mean / <=44 max with xq.full 85-91 %, `l1d_pend_miss.l2_stalls` 19 % in mix_t2x2.
4. **Separate cap on outstanding demand reads / demand FBs**: demand never > 23 outstanding, demand FB
   <= 16; the remainder are hinted requests. Needed to get ~20 in rand_dram and ~25 in mixes with loads
   while prefetch-only mixes reach 37.
5. **Drop prefetcht2 when neither an FB nor an L2 request slot is available** (0.21 of 2 per iteration in
   mix_t2x2, coinciding with l2_stalls 19 %); currently memsys never drops.
6. **Blocked loads/prefetches leave the scheduler** (load buffer holds them; RS empty 60-65 % in
   prefetch-only mixes) — affects how deep the window is when FBs are full (ROB 512 rather than the
   scheduler size); interacts with the load-buffer work being done in parallel.
7. Lower priority: DRAM latency variance under load (PEBS RAM-hit p90 = 338 alone vs 367-456 in
   rand/mixes, 600+ in mchase48), cancelled speculative dispatch of address-dependent loads
   (1.4-1.9 dispatches per load in chases; port pressure only).

## PEBS load latency (dispatch -> completion, cycles: n samples, mean, p50, p90)

| test | instruction | data source | n | mean | p50 | p90 |
|---|---|---|---:|---:|---:|---:|
| chase_dram | load | RAM | 743 | 312 | 298 | 338 |
| rand_dram | load | RAM | 1445 | 326 | 311 | 367 |
| rand_dram | load | L2 (hinted) | 164 | 345 | 344 | 382 |
| pf_t0_dram | prefetcht0 | (L1) | 3043 | 150 | 150 | 268 |
| pf_t0_dram | load | L1 | 453 | 5 | 5 | 5 |
| pf_t0_dram | load | LFB | 211 | 152 | 147 | 298 |
| pf_t2_dram | prefetcht2 | (L1) | 2419 | 52 | 24 | 146 |
| pf_t2_dram | load | L2 | 3842 | 94 | 51 | 227 |
| mix_t2x2 | prefetcht2 a / b | | 3511 / 3370 | 141 / 146 | 104 / 106 | 318 / 330 |
| mix_t0_t2x1 | prefetcht0 / prefetcht2 | | 3279 / 3388 | 216 / 47 | 217 / 37 | 372 / 91 |
| mix_ld_t2x1 | load RAM / prefetcht2 | | 3644 / 2793 | 333 / 96 | 315 / 63 | 380 / 241 |
| mix_ld_t2x2 | load RAM / t2 / t2 | | 1848 / 1356 / 1238 | 357 / 92 / 100 | 329 / 61 / 69 | 456 / 229 / 236 |
| mix_ld_t0x1 | load RAM / load L2 / prefetcht0 | | 3128 / 791 / 2990 | 337 / 376 / 206 | 318 / 366 / 221 | 393 / 443 / 349 |
| mchase16/32/48 | chain loads, RAM | | | ~300-365 / 305-405 / 296-456 per chain | | up to 617 / 762 / 946 |

(The prefetch rows are reported by PEBS as "L1 hit" — software prefetches always are — so their latency is
the wait until the prefetch completes. Sampled loads were attributed via `objdump -d build/harness`.)

## All events with a non-trivial value on at least one test
| event (per iteration) | chase | rand | pf_t0 | pf_t2 | t2x2 | ld_t2 | ld_t2x2 | t0_t2 | ld_t0 | mc16 | mc32 | mc48 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cycle_activity.cycles_l2_miss | 301 | 15.1 | 0.04 | 2.90 | 0.02 | 24.3 | 36.3 | 0.06 | 24.9 | 338 | 430 | 613 |
| cycle_activity.cycles_mem_any | 316 | 15.2 | 10.2 | 11.3 | 0.07 | 24.5 | 36.6 | 0.09 | 25.1 | 339 | 432 | 615 |
| cycle_activity.stalls_total | 314 | 11.9 | 8.36 | 6.95 | 11.0 | 19.3 | 29.5 | 12.2 | 19.8 | 311 | 355 | 509 |
| cycles | 316 | 15.1 | 13.2 | 11.5 | 15.2 | 24.7 | 33.5 | 16.9 | 25.2 | 340 | 435 | 620 |
| dtlb_load_misses.walk_active | 0.02 | 0.00 | 0.00 | 0.01 | 0.01 | 0.00 | 0.00 | 0.00 | 0.00 | 0.09 | 0.23 | 0.27 |
| exe_activity.1_ports_util | 2.15 | 1.42 | 1.62 | 1.35 | 0.93 | 2.01 | 2.40 | 1.45 | 2.12 | 7.50 | 47.4 | 59.3 |
| exe_activity.bound_on_loads | 314 | 11.9 | 5.73 | 6.94 | 0.05 | 19.3 | 29.4 | 0.06 | 19.8 | 310 | 353 | 506 |
| exe_activity.bound_on_stores | 0.01 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.03 | 0.12 | 0.19 |
| exe_activity.exe_bound_0_ports | 0.07 | 0.00 | 0.31 | 0.01 | 1.15 | 0.01 | 0.03 | 1.89 | 0.01 | 0.12 | 0.16 | 0.26 |
| instructions | 3.89 | 8.06 | 14.0 | 14.0 | 13.0 | 13.1 | 18.1 | 13.1 | 13.1 | 52.0 | 101 | 152 |
| int_misc.recovery_cycles | 0.02 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.04 | 0.07 | 0.11 |
| int_misc.uop_dropping | 0.09 | 0.32 | 0.53 | 0.36 | 0.49 | 0.52 | 0.72 | 0.73 | 0.56 | 5.39 | 11.9 | 17.1 |
| l1d.replacement | 1.02 | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 2.00 | 16.0 | 32.1 | 48.1 |
| l1d_pend_ge12 | 0.00 | 11.7 | 0.00 | 0.00 | 0.00 | 3.97 | 0.26 | 0.00 | 0.01 | 288 | 387 | 532 |
| l1d_pend_ge16 | 0.00 | 5.30 | 0.00 | 0.00 | 0.00 | 0.01 | 0.00 | 0.00 | 0.00 | 20.9 | 122 | 188 |
| l1d_pend_ge4 | 0.03 | 14.6 | 0.03 | 0.38 | 0.00 | 23.0 | 33.4 | 0.01 | 23.4 | 335 | 425 | 601 |
| l1d_pend_ge8 | 0.00 | 13.7 | 0.00 | 0.00 | 0.00 | 19.3 | 13.4 | 0.00 | 9.47 | 329 | 417 | 585 |
| l1d_pend_miss.fb_full | 0.00 | 10.6 | 11.9 | 7.72 | 14.4 | 16.0 | 22.8 | 16.1 | 20.3 | 216 | 372 | 518 |
| l1d_pend_miss.fb_full_periods | 0.00 | 0.19 | 0.21 | 0.31 | 0.07 | 0.25 | 0.31 | 0.17 | 0.39 | 6.80 | 6.75 | 10.0 |
| l1d_pend_miss.l2_stalls | 0.00 | 0.01 | 0.01 | 0.05 | 2.83 | 0.02 | 0.04 | 0.28 | 0.04 | 0.08 | 0.79 | 1.97 |
| l1d_pend_miss.pending | 310 | 199 | 9.44 | 12.4 | 0.05 | 219 | 246 | 0.13 | 168 | 4456 | 6031 | 8439 |
| l1d_pend_miss.pending_cycles | 310 | 15.1 | 7.12 | 7.36 | 0.03 | 24.4 | 36.4 | 0.06 | 25.0 | 338 | 431 | 614 |
| l2_lines_in.all | 1.01 | 1.00 | 1.00 | 1.00 | 1.78 | 1.99 | 2.98 | 1.98 | 2.00 | 16.1 | 32.1 | 48.2 |
| l2_lines_out.non_silent | 1.00 | 1.00 | 1.00 | 1.00 | 1.78 | 1.99 | 2.98 | 1.97 | 2.00 | 16.0 | 32.0 | 48.1 |
| l2_lines_out.silent | 0.01 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.08 | 0.11 | 0.18 |
| l2_lines_out.useless_hwpf | 0.00 | 0.00 | 0.00 | 0.00 | 1.26 | 0.66 | 1.27 | 0.86 | 0.00 | 0.00 | 0.00 | 0.00 |
| l2_rqsts.all_demand_data_rd | 1.02 | 0.48 | 0.00 | 0.99 | 0.00 | 0.42 | 0.35 | 0.00 | 0.50 | 9.15 | 14.5 | 23.6 |
| l2_rqsts.all_demand_miss | 1.01 | 0.36 | 0.00 | 0.01 | 0.00 | 0.37 | 0.32 | 0.00 | 0.29 | 8.97 | 9.07 | 13.4 |
| l2_rqsts.all_hwpf | 0.00 | 0.64 | 0.78 | 1.18 | 1.29 | 1.30 | 1.98 | 1.68 | 1.46 | 7.10 | 23.0 | 34.7 |
| l2_rqsts.demand_data_rd_hit | 0.02 | 0.12 | 0.00 | 0.98 | 0.00 | 0.05 | 0.03 | 0.00 | 0.22 | 0.25 | 5.52 | 10.3 |
| l2_rqsts.demand_data_rd_miss | 1.00 | 0.37 | 0.00 | 0.01 | 0.00 | 0.37 | 0.33 | 0.00 | 0.28 | 8.90 | 8.98 | 13.3 |
| l2_rqsts.hwpf_miss | 0.00 | 0.64 | 0.78 | 0.57 | 1.29 | 1.30 | 1.98 | 1.68 | 1.46 | 7.10 | 23.0 | 34.7 |
| l2_rqsts.miss | 1.01 | 1.00 | 1.00 | 1.00 | 1.78 | 1.99 | 2.98 | 1.98 | 2.00 | 16.1 | 32.1 | 48.2 |
| l2_rqsts.references | 1.04 | 1.12 | 1.08 | 2.60 | 1.79 | 2.04 | 3.01 | 2.07 | 2.39 | 16.4 | 37.7 | 58.5 |
| l2_rqsts.swpf_hit | 0.00 | 0.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.10 | 0.17 | 0.00 | 0.00 | 0.00 |
| l2_rqsts.swpf_miss | 0.00 | 0.00 | 0.22 | 0.43 | 0.49 | 0.32 | 0.71 | 0.29 | 0.26 | 0.00 | 0.00 | 0.00 |
| load_hit_prefetch.swpf | 0.00 | 0.00 | 0.07 | 0.01 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| mem_inst_retired.all_loads | 1.23 | 1.01 | 2.01 | 2.01 | 2.01 | 2.02 | 3.02 | 2.01 | 2.02 | 32.3 | 64.6 | 96.9 |
| mem_load_completed.l1_miss_any | 1.03 | 1.00 | 0.07 | 1.00 | 0.00 | 1.00 | 1.00 | 0.00 | 1.00 | 16.0 | 32.1 | 48.1 |
| mem_load_l3_miss_retired.local_dram | 1.00 | 0.88 | 0.00 | 0.02 | 0.00 | 0.95 | 0.97 | 0.00 | 0.78 | 15.7 | 26.4 | 37.5 |
| mem_load_retired.fb_hit | 0.02 | 0.01 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.07 | 0.21 | 0.30 |
| mem_load_retired.l1_hit | 0.21 | 0.01 | 1.94 | 1.01 | 2.01 | 1.02 | 2.02 | 2.01 | 1.02 | 16.3 | 32.9 | 48.9 |
| mem_load_retired.l1_miss | 1.02 | 1.00 | 0.00 | 1.00 | 0.00 | 1.00 | 1.00 | 0.00 | 1.00 | 16.0 | 31.9 | 47.8 |
| mem_load_retired.l2_hit | 0.02 | 0.11 | 0.00 | 0.98 | 0.00 | 0.05 | 0.02 | 0.00 | 0.22 | 0.24 | 5.48 | 10.2 |
| mem_load_retired.l2_miss | 1.00 | 0.88 | 0.00 | 0.02 | 0.00 | 0.95 | 0.98 | 0.00 | 0.78 | 15.7 | 26.4 | 37.6 |
| mem_load_retired.l3_hit | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.01 | 0.00 | 0.00 | 0.03 | 0.04 | 0.05 |
| mem_load_retired.l3_miss | 1.00 | 0.88 | 0.00 | 0.02 | 0.00 | 0.95 | 0.97 | 0.00 | 0.78 | 15.7 | 26.4 | 37.5 |
| mem_uop_retired.any | 1.33 | 1.02 | 2.02 | 2.02 | 2.02 | 2.03 | 3.04 | 2.03 | 2.03 | 48.6 | 97.0 | 145 |
| memory_activity.cycles_l1d_miss | 310 | 15.1 | 7.13 | 7.36 | 0.02 | 24.3 | 36.4 | 0.06 | 25.0 | 339 | 431 | 615 |
| memory_activity.stalls_l1d_miss | 308 | 12.2 | 5.23 | 4.50 | 0.02 | 19.6 | 29.8 | 0.04 | 20.2 | 310 | 360 | 512 |
| memory_activity.stalls_l2_miss | 301 | 12.2 | 5.23 | 2.59 | 0.02 | 19.6 | 29.8 | 0.04 | 20.2 | 310 | 360 | 512 |
| memory_activity.stalls_l3_miss | 247 | 12.0 | 2.48 | 1.78 | 0.01 | 19.1 | 28.6 | 0.01 | 18.9 | 309 | 357 | 504 |
| ocr.demand_data_rd.any_response | 1.00 | 0.86 | 0.00 | 0.02 | 0.00 | 0.93 | 0.96 | 0.00 | 0.75 | 15.7 | 26.0 | 36.7 |
| ocr.hwpf_l1d.any_response | 0.00 | 0.00 | 0.69 | 0.00 | 0.02 | 0.00 | 0.00 | 0.70 | 0.54 | 0.00 | 0.00 | 0.00 |
| ocr.hwpf_l2.any_response | 0.00 | 0.14 | 0.09 | 0.55 | 1.27 | 0.74 | 1.33 | 0.98 | 0.45 | 0.28 | 6.05 | 11.3 |
| ocr.reads_to_core.any_response | 1.01 | 1.00 | 1.00 | 1.00 | 1.79 | 1.99 | 2.98 | 1.97 | 2.00 | 16.1 | 32.1 | 48.2 |
| offcore_requests.all_requests | 2.01 | 2.00 | 2.00 | 2.00 | 3.57 | 3.98 | 5.96 | 3.95 | 4.00 | 32.1 | 64.1 | 96.2 |
| offcore_requests.data_rd | 1.00 | 1.00 | 1.00 | 1.00 | 1.78 | 1.99 | 2.98 | 1.97 | 2.00 | 16.0 | 32.0 | 48.1 |
| offcore_requests.demand_data_rd | 1.00 | 0.88 | 0.00 | 0.02 | 0.00 | 0.95 | 0.98 | 0.00 | 0.78 | 15.8 | 26.5 | 37.8 |
| offcore_requests.l3_miss_demand_data_rd | 0.99 | 0.88 | 0.00 | 0.02 | 0.00 | 0.95 | 0.97 | 0.00 | 0.78 | 15.7 | 26.5 | 37.7 |
| offcore_requests_outstanding.cycles_with_data_rd | 301 | 15.1 | 13.1 | 11.3 | 15.0 | 24.4 | 36.6 | 16.8 | 25.1 | 338 | 432 | 613 |
| offcore_requests_outstanding.data_rd | 301 | 305 | 302 | 310 | 560 | 623 | 970 | 613 | 624 | 4852 | 9866 | 14989 |
| offcore_requests_outstanding.demand_data_rd | 301 | 188 | 0.07 | 3.31 | 0.03 | 209 | 234 | 0.10 | 156 | 4307 | 5681 | 7879 |
| offcore_requests_outstanding.l3_miss_demand_data_rd | 246 | 164 | 0.01 | 2.88 | 0.01 | 182 | 209 | 0.02 | 137 | 3668 | 5036 | 6954 |
| osr_data_rd_ge16 | 0.00 | 10.3 | 12.0 | 10.5 | 15.0 | 19.8 | 30.1 | 16.8 | 20.2 | 118 | 377 | 516 |
| osr_data_rd_ge24 | 0.00 | 6.03 | 6.49 | 8.29 | 15.0 | 14.6 | 23.4 | 16.7 | 14.4 | 0.00 | 237 | 366 |
| osr_data_rd_ge32 | 0.00 | 2.00 | 0.40 | 3.48 | 14.8 | 8.54 | 14.1 | 15.8 | 7.79 | 0.00 | 7.73 | 141 |
| osr_data_rd_ge40 | 0.00 | 0.00 | 0.00 | 0.34 | 2.92 | 1.05 | 3.71 | 2.73 | 0.78 | 0.00 | 0.00 | 1.38 |
| osr_data_rd_ge44 | 0.00 | 0.00 | 0.00 | 0.05 | 0.09 | 0.00 | 0.16 | 0.18 | 0.00 | 0.00 | 0.00 | 0.00 |
| osr_data_rd_ge8 | 0.00 | 13.5 | 13.0 | 11.1 | 15.0 | 22.7 | 33.9 | 16.8 | 23.4 | 328 | 415 | 581 |
| osr_demand_rd_ge16 | 0.00 | 4.40 | 0.00 | 0.00 | 0.00 | 0.01 | 0.00 | 0.00 | 0.00 | 14.5 | 81.5 | 135 |
| osr_demand_rd_ge8 | 0.00 | 13.2 | 0.00 | 0.00 | 0.00 | 18.0 | 11.8 | 0.00 | 7.71 | 327 | 412 | 573 |
| resource_stalls.sb | 0.02 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.05 | 0.16 | 0.30 |
| resource_stalls.scoreboard | 0.40 | 0.04 | 0.03 | 0.02 | 0.02 | 0.04 | 0.06 | 0.05 | 0.06 | 1.63 | 1.64 | 2.48 |
| rs.empty | 0.23 | 0.01 | 2.33 | 0.01 | 9.79 | 0.03 | 0.10 | 10.2 | 0.03 | 0.89 | 1.33 | 2.31 |
| sw_prefetch_access.any | 0.00 | 0.00 | 1.00 | 1.00 | 2.00 | 1.00 | 2.00 | 2.00 | 1.00 | 0.00 | 0.00 | 0.00 |
| sw_prefetch_access.t0 | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 |
| sw_prefetch_access.t1_t2 | 0.00 | 0.00 | 0.00 | 1.00 | 2.00 | 1.00 | 2.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| topdown.backend_bound_slots | 212 | 36.7 | 26.1 | 22.2 | 32.3 | 58.8 | 88.6 | 36.4 | 60.5 | 891 | 1089 | 1552 |
| topdown.bad_spec_slots | 0.24 | 0.02 | 0.01 | 0.01 | 0.01 | 0.02 | 0.03 | 0.02 | 0.02 | 0.55 | 1.18 | 1.73 |
| topdown.memory_bound_slots | 209 | 31.3 | 13.1 | 16.8 | 0.08 | 49.7 | 75.9 | 0.10 | 50.8 | 824 | 908 | 1305 |
| topdown.slots_p | 1567 | 47.2 | 45.2 | 40.0 | 49.9 | 77.0 | 115 | 55.1 | 78.8 | 983 | 1287 | 1845 |
| uops_dispatched.port_2_3_10 | 2.29 | 1.02 | 2.01 | 2.01 | 2.02 | 2.02 | 3.03 | 2.02 | 2.02 | 47.0 | 88.3 | 97.7 |
| uops_executed.thread | 3.04 | 7.06 | 11.1 | 11.0 | 11.1 | 12.1 | 17.1 | 11.1 | 12.1 | 67.1 | 132 | 198 |
| uops_issued.any | 3.35 | 8.19 | 14.9 | 14.2 | 13.7 | 13.9 | 19.6 | 14.7 | 14.1 | 71.7 | 146 | 215 |
| xq.full_cycles | 0.00 | 0.00 | 0.00 | 1.01 | 13.8 | 0.67 | 2.73 | 14.3 | 0.28 | 0.05 | 6.98 | 55.4 |

Trivial everywhere (< 0.05 per iteration on every test): `dtlb_load_misses.stlb_hit`, `dtlb_load_misses.walk_completed`, `l1d.hwpf_miss`, `l2_rqsts.all_rfo`, `ld_blocks.address_alias`, `ld_blocks.no_sr`, `ld_blocks.store_forward`, `machine_clears.memory_ordering`, `mem_inst_retired.stlb_miss_loads`, `osr_data_rd_ge48`, `osr_demand_rd_ge24`, `sw_prefetch_access.prefetchw`

## Per-event notes (mechanism / modelled in memsys? / relevance to the gaps)

| event(s) | mechanism | in memsys? | relevance |
|---|---|---|---|
| `l2_rqsts.hwpf_miss`, `all_hwpf`, `ocr.hwpf_l2/l1d.any_response`, `l2_rqsts.demand_data_rd_miss`, `all_demand_miss` | FB-full L1 misses sent to L2 as hints (sec. 1) | option `fbFullL2Hint`, off | primary: rand, mchase32-48, pf_t0, pf_t2 |
| `l2_rqsts.swpf_miss/hit` | software prefetches that got an FB (documented "when FB is not full") | implicitly (FB path) | gives the non-hinted fraction per type |
| `mem_load_retired.l2_hit`, `l2_rqsts.demand_data_rd_hit` | hinted loads completing as late L2 hits; pf_t2 loads | no (hint path off) | mchase (17-21 % of loads), rand (11 %) |
| `l1d_pend_miss.fb_full`, `fb_full_periods` | cycles a request waits for an FB; mean period 32 (mchase16) to 55 (rand, pf_t0, mchase48) to 200 cycles (mix_t2x2) | yes (blockedCycles) | calibration target |
| `l1d_pend_miss.pending`, `l1d_pend_geN` | demand-held FB occupancy (max 16) | yes (FB station) | FB hold per load 167-278 cycles |
| `offcore_requests_outstanding.*`, `osr_*_geN`, `offcore_requests.*`, `ocr.reads_to_core` | L2-miss queue occupancy distribution; ceiling ~44-47; demand <= 23 | L2Q station (48) | sec. 3 |
| `xq.full_cycles` | uncore request queue full | no | prefetch-only mixes (85-91 %), mchase48 9 % |
| `l1d_pend_miss.l2_stalls` | L1 request waiting for L2 resources | no | mix_t2x2 19 %, coincides with dropped t2 |
| `sw_prefetch_access.*`, `l2_rqsts.references`, `l2_lines_in.all` | executed prefetches vs requests reaching L2 | no drops modelled | 0.21 t2/iter dropped in mix_t2x2 |
| `l2_lines_out.useless_hwpf` | hinted prefetcht2 lines evicted unused | n/a | none |
| `l1d.replacement`, `l2_lines_out.non_silent` | one L1 / L2 fill+eviction per miss; ld_t0 has 2 L1 fills/iter | yes (LRU) | none |
| `uops_dispatched.port_2_3_10`, `mem_inst_retired.all_loads`, `mem_uop_retired.any`, `mem_load_completed.l1_miss_any` | dispatches per load = 1.00 when FB-blocked (no replay); 1.4-1.9 for address-dependent chase loads | uiCA: blocked uops stay in RS | sec. 1; port pressure only |
| `load_hit_prefetch.swpf`, `mem_load_retired.fb_hit` | demand load merging with an in-flight prefetch FB (pf_t0 0.07, pf_t2 0.013) | yes ('FB' level) | pf_t0: fewer merges than PEBS LFB class suggests |
| `rs.empty`, `exe_activity.*`, `cycle_activity.*`, `memory_activity.*`, `topdown.*` | where stalls come from: memory-bound 64-84 % of slots in load tests; prefetch-only mixes stall with RS empty (ROB/LB full of waiting prefetches) | partly (uiCA ROB/RS) | sec. 1, item 6 |
| `memory_activity.stalls_l3_miss` vs `stalls_l2_miss` | chase_dram 247 of 301 stall cycles "known L3 miss": the core learns of the L3 miss ~55 cycles into the trip | no | none (latency is calibrated end-to-end) |
| `dtlb_load_misses.walk_active` (<=0.27 cycles/iter), `resource_stalls.sb`, `resource_stalls.scoreboard`, `int_misc.uop_dropping` | small: TLB, store buffer, serialisation, front-end drops | no | negligible |
