# LFB / XQ rules on Xeon Max 9462 (SPR HBM): method and first results

Goal: find the rules the core follows when it sends L1 misses (demand loads, prefetcht0, prefetcht1) to memory, and
build them into an emulator (tools/uiCA). Each rule says which structure a request holds, for how long, and what
happens when that structure is full.

## 1. Method: account for every request's path

Treat the core as logic, and each counter as a probe on one wire of it. For one instruction type (here one
prefetch per loop iteration):

1. **Count the input.** Count the instructions that start requests (`sw_prefetch_access.t0` / `.t1_t2`).
2. **Split by path.** Use counters that are mutually exclusive paths for the same request, and divide each by the
   input count. Example: at L2, an L1 miss arrives either as `l2_rqsts.swpf_miss` (sent with a fill buffer) or as
   `l2_rqsts.hwpf_miss` (sent without one, labelled as a hardware prefetch).
3. **Check that the paths add up.** The path fractions must sum to 1, and each later stage must see the same lines:
   L1 fills (`l1d.replacement`), L2 misses (`offcore_requests.data_rd`), HBM reads (uncore CAS counts).
   A sum below 1 means requests were dropped or merged. A count above 1 at a later stage means extra traffic.
   When the loop also has a demand load on the prefetched line, L1 fills don't show where the prefetch delivered
   its line, because the demand load fills L1 anyway. Use where the demand load was served
   (`mem_load_retired.l1_hit / fb_hit / l1_miss / l2_hit`, per iteration), and read the disassembly to know
   which loads these counters see.
4. **Turn the paths into occupancy with Little's law.** For each structure of known size N: N >= rate x (time
   each request holds it), summed over the paths. Measured: rate (HBM counters), trip time W
   (`offcore_requests_outstanding / offcore_requests`), and the cycles a structure is full (`fb_full`,
   `xq.full`). Any hold time the counters cannot see directly is solved from that budget, and must agree with
   the measured number in flight.

A rule is accepted only when the conservation check in step 3 closes, and the step 4 budget matches the measured
in-flight count without a free parameter.

## 2. Harness

- `bw_roi.c` = `machine_stats/bandwidth.c` + perf ROI control (env `PERF_CTL` / `PERF_ACK`, colon-separated
  fifo lists). Counting starts 1 s after the start barrier and stops when the first worker leaves its loop.
  Build: `gcc -O3 -march=native bw_roi.c -lpthread -lnuma -o bw_roi -DRANDOM`.
- `run_roi.sh <inst> <ncores> <tag>` runs two perf processes that share the ROI:
  - core events: `-C <worker cpus>`, 3 fixed + **at most 4 programmable** events (more than that rotates on this
    machine; the NMI watchdog holds one counter). Choose the list with `CORE=...`.
  - uncore events: `-a --per-socket`, HBM CAS on all 32 boxes (`event=0x05,umask=0xcf` reads,
    `umask=0xf0` writes; 32 B each) and the CHA clock. In the same process as the core events the HBM counters
    rotated (67 %), and their 1 ms timers on CPU 0 slowed worker 0 by about 2x, so they are kept separate.
- `an_roi.py` (frequency, HBM GB/s, Little's law) and `an_hint.py` (path split) read `logs/<tag>.*`.
- Workload: `-m 1gb -pattern n0a2t8 -inst {t0|t1} -lookahead 64 -mode r`: 8 physical cores (CPUs 0,2,...,14,
  hyperthread siblings idle), random lines on HBM node 2. Per line: crc32 hash, prefetch line i+64, demand load
  line i.
- Machine state: hardware prefetchers off (MSR 0x1a4 = 0x2f; was 0x20), turbo off, uncore set to 2.5 GHz.

Calculated quantities (Σ = total over the ROI and the worker CPUs):

| quantity | formula |
|---|---|
| core GHz | 2.7 x Σcycles / Σref-cycles |
| mesh GHz | clk_cha(socket 0) / ROI seconds |
| HBM GB/s | 32 B x Σhbm_rd(socket 0) / ROI seconds |
| λ = lines/s per core | HBM read bytes / 64 / ROI / ncores |
| N = in flight per core | Σoffcore_requests_outstanding.data_rd / Σcycles |
| W = trip time per L2 miss | Σoffcore_requests_outstanding.data_rd / Σoffcore_requests.data_rd / core GHz |
| path fraction | Σpath counter / Σprefetch instructions |

## 3. Results (8x1, core 2.694 GHz, one run each)

| | t1 | t0 |
|---|---|---|
| HBM read GB/s | 99.3 | 88.1 |
| λ lines/s per core | 194 M | 172 M |
| N in flight per core / W | 28.7 / 147 ns | 24.8 / 142 ns |
| fb_full / xq.full | 63 % / 18 % | 94 % / 0 % |
| `l2_rqsts.hwpf_miss` / prefetch (sent without an FB) | **0.587** | **0.818** |
| `l2_rqsts.swpf_miss` / prefetch (sent with an FB) | 0.406 | 0.182 |
| sum | 0.993 | 1.000 |
| `l1d.replacement` / prefetch | 1.001 | 1.001 |
| HBM lines / prefetch | 0.987 | 0.988 |

(N, W and the fb/xq fractions come from the `roi_*` runs; the path split from the `hint_*` runs.)

### Rules supported so far
1. **An L1 miss that finds all fill buffers busy is still sent out at once, as an L2 "hardware prefetch".** The
   L2 opens its own miss entry, and the read goes to the uncore through the XQ. It holds no fill buffer while it
   travels. With hardware prefetchers off, 82 % (t0) and 59 % (t1) of prefetch L2 misses take this path. The same
   path was found on EMR (`tools/uiCA/tests/results/counter_survey.md`: 0.78 / 0.57).
2. **Every t0, including the 82 % sent without an FB, brings its line into L1 before the demand load arrives.**
   `l1d.replacement` = 1 per line cannot show this, because the loop's demand load (`add (%rdi,%rax),%r15`,
   64 iterations after the prefetch, about 1000 cycles) would fill L1 anyway. The evidence is where the demand
   load is served (`dld_*` runs, `an_dload.py`; per iteration of 14 instructions):

   | `mem_load_retired.*` / iteration | t0 | t1 |
   |---|---|---|
   | l1_hit | 1.995 | 1.005 |
   | fb_hit | 0.008 | 0.002 |
   | l1_miss | 0.001 | 0.972 |
   | l2_hit | 0.000 | 0.971 |

   These counters also count the SW prefetch as a retired load, as an L1 hit: in t1 the demand load must miss L1
   (0.97 L2 hits), and the other 1.0 L1 hit can only be the prefetcht1. So for t0, 0.995 of 1 demand loads hit L1,
   0.8 % find the line still in flight (fb_hit), and none go to L2. No t0 is dropped or left only in L2. HBM sends
   0.985 lines per iteration (the 1.5 % gap is about the LLC's share of the 8 GB footprint).
   With t1 the demand load is an L2 hit 97 % of the time and needs an FB for the L2 -> L1 refill.
3. **A 16-FB Little's law reproduces t0 only with rule 1.** If every t0 held an FB for the whole trip:
   λ = 16 / (142 ns) = 113 M lines/s per core = 57.7 GB/s for 8 cores, against 88.1 measured (-35 %).
   With rule 1: FB budget per line = 16 x 15.5 cycles = 248 FB-cycles. The 18 % sent with an FB hold one for the
   whole trip (about 394 cycles, 72 FB-cycles per line). That leaves about 215 cycles for each early-sent t0, so it
   gets its FB about 180 cycles after its request left. Requests out without an FB: 0.0646 lines/cycle x 0.818 x
   180 = 9.5, so in flight = 16 + 9.5 = 25.5, against 24.8 measured.
4. **For t1, about 0.7 % of prefetches appear in neither L2 counter** (sum 0.993). They may have been dropped
   (EMR showed prefetcht2 drops when there is neither an FB nor an L2 slot). One run, so not yet a firm finding.

### Not yet known
- What decides the split: 82 % against fb_full 94 % for t0.
- Where an early-sent t0 waits for its FB, and what caps the number waiting (about 9 per core): the load buffer,
  the ROB (512 / 14 instructions per line ≈ 36 lines), or the L2 miss queue. EMR evidence: the waiting uop is not
  re-dispatched, waits outside the RS, and blocks retirement. A t0 completes only once it owns an FB; a t1
  completes once its request has been sent.
- Whether fill buffers are handed out one at a time or in batches (`l1d_pend_miss.fb_full_periods` gives how long
  each full episode lasts).

## 4. Facts so far (session of 2026-11-19)

Measured (8x1, random HBM reads, HW prefetchers off, core 2.694 GHz, one run each):
1. t1 gets 13 % more bandwidth than t0 at the same latency: HBM 99 vs 88 GB/s, trip 147 vs 142 ns.
   The whole difference is in the uncore buffer U = λ x W: 28.5 vs 24.6 requests in flight per core.
2. 16 FBs held for the whole trip cannot give t0's bandwidth: 16 / 142 ns -> 57.7 GB/s, against 88 measured.
   About 9 requests per core are in flight beyond the 16.
3. When the FBs are full the request still goes out: the L2 counts 82 % (t0) / 59 % (t1) of prefetches as
   `hwpf_miss` with HW prefetchers off. The rest (18 % / 41 %) are `swpf_miss`, sent with an FB.
   Together they sum to 1.00 / 0.99.
4. No t0 is lost: 99.5 % of the following demand loads hit L1, 0.8 % are FB hits, 0 % go to L2.
5. Every t1 line reaches L2 (97 % of demand loads are L2 hits). HBM lines per prefetch: 0.985-0.988 for both.
6. fb_full 94 % (t0) vs 63 % (t1); xq.full 0 % vs 18 %.

Inferred (from measurements + fbmodel.py):
7. A t1 that gets an FB at dispatch holds it for about the whole trip (until the line is in L2). With a short hold
   the FBs would hardly ever be full, giving p = 0 instead of 0.59.
8. FB arbitration matters. Waiters first cannot reproduce t0. New requests first reproduces throughput and U for
   t0 and t1, but its p is about 0.12 too low for both.
9. The 192-entry load buffer never limits this loop; the ROB window does first.
10. From batch_test (user's earlier observation, not re-measured here): the 17th t1 in a batch stalls the CPU.
    So an overflowed t1 must wait for something before it can retire. An FB is the simplest candidate, but not
    yet shown.

Open: (a) the order in which a freed FB goes to old vs new requests; (b) what an overflowed t1 waits for, and for
how long. Next measurement: PEBS dispatch-to-completion latency of t0 / t1 at 8x1.

### Prefetch-only t1 (`BW_PFONLY=1`, no demand load; `pfo_t1_8x1`, `pfohint_t1_8x1`)
11. Without the demand load, t1 gets 127.7 GB/s (vs 98.9), U = 36.1 in flight per core (vs 28.7), W = 144 ns.
    So ~29 is not a hard per-thread cap. In the normal loop the demand loads' FB refills cost about 7 slots of U.
12. About 8-9 % of t1s are dropped: (hwpf + swpf) / t1 = 0.916 and HBM lines / t1 = 0.904 (0.99 with the
    demand load). Not yet ruled out: L2 hits (`l2_rqsts.swpf_hit`).
13. fb_full 96 % with no demand loads at all (`l1d.replacement` = 0), so `l1d_pend_miss.fb_full` also counts
    prefetches waiting for an FB. But xq.full is 94 % too, so this does not yet show whether an overflowed t1
    waits for an FB or the FBs are held because the XQ is full. Next: prefetch-only t1 with padding (lower
    issue rate, XQ not full) and see whether fb_full stays high.

### Prefetch-only t0 (`pfo_t0_8x1`, `pfohint_t0_8x1`)
14. Prefetch-only t0: 111.9 GB/s, U = 33.0 (vs 24.6 with the demand load), W = 150 ns, fb_full 94 %, xq.full 21 %.
    hwpf / swpf = 0.836 / 0.164, sum 1.000, HBM lines / t0 = 0.987, `l1d.replacement` / t0 = 1.001.
15. Measured: an overflowed t0 comes back for an FB. With no demand load only the t0 can fill L1, and every t0
    does (1.001), although 84 % left without an FB. No t0 is dropped (t1 drops about 8 % when xq.full is 94 %).
16. Removing t0's demand load (an L1 hit, no FB) raises U from 24.6 to 33.0, so FBs were not the only limit.
    Hypothesis, not tested: the ROB window. A waiting t0 blocks retirement; 512 / 14 = 36 iterations fit with
    the demand load vs 512 / 8 = 64 without.
17. Prefetch-only, t1 still beats t0: 249 vs 219 M lines/s per core (U 36.1 vs 33.0).

### 1 core x 1 thread (`*_1x1`; CPU 0, -m 1gb, mesh 2.50 GHz)
With one core the footprint is 1 GB and the 75 MB LLC serves 6-13 % of the L2 misses, so λ is taken from
`offcore_requests.data_rd` (L2 misses), not HBM, and W mixes LLC hits with HBM trips.

| | t0 + ld | t0 only | t1 + ld | t1 only |
|---|---|---|---|---|
| λ = L2 misses/s | 185 M | 242 M | 212 M | 284 M |
| U in flight (8x1 value) | 23.4 (24.6) | 29.3 (33.0) | 26.6 (28.7) | 35.0 (36.1) |
| W | 127 ns | 121 ns | 126 ns | 123 ns |
| fb_full / xq.full | 93 / 0 % | 93 / 15 % | 59 / 15 % | 95 / 92 % |
| hwpf / swpf per prefetch | 0.802 / 0.198 | 0.826 / 0.174 | 0.579 / 0.413 | 0.652 / 0.268 |
| sum | 1.000 | 1.000 | 0.993 | 0.920 |
| l1d.replacement / prefetch | 1.001 | 1.001 | 1.001 | 0.000 |

18. U is a property of the core: within 3-11 % of 8x1, same order. Less uncore load only shortens W (~125 vs ~145 ns).
19. The path split (hwpf share) is the same as at 8x1 to within 0.02, so it does not depend on uncore load.
20. Prefetch-only t1 again drops ~8 % (xq.full 92 %); t0 never drops, and every prefetch-only t0 fills L1.

### Padding sweep + PEBS (prefetch-only, 1x1; `pfo*_p*_1x1`, `pebs_*`; `PAD=N` for run_roi.sh, `pebs.sh`, `an_pebs.py`)
Pad = N dependent `add $1` per line after the prefetch. It slows the loop much less than 1 cycle per add (t1:
pad 16 -> 9.9 cycles/line, pad 32 -> 13.7; t0: pad 16 -> 16.8). Cycles per line below are measured.

| | t1 p0 | t1 p16 | t1 p32 | t0 p0 | t0 p16 |
|---|---|---|---|---|---|
| U / xq.full / fb_full | 35.0 / 92 / 95 % | 33.1 / 62 / 67 % | 23.5 / 0 / 52 % | 29.3 / 15 / 93 % | 19.1 / 0 / 88 % |
| hwpf / swpf / sum | .652/.268/.920 | .608/.391/1.000 | .423/.577/1.000 | .826/.174/1.000 | .730/.270/1.000 |
| PEBS dispatch->completion mean / p50 / p90 (cycles) | 123 / 82 / 305 | - | 20 / 5 / 59 | 209 / 218 / 390 | 86 / 66 / 207 |
| PEBS share > 8 cycles / > 50 cycles | 88 / 64 % | - | 33 / 12.5 % | 83 / 78 % | 72 / 56 % |

21. An overflowed t1 waits before it completes, even with the XQ never full (t1 p32): fb_full 52 % with no demand
    loads, 42 % of t1 overflow, and 33 % of t1 take 10-130 cycles to complete instead of ~5. Answers open question
    (b) with moderate confidence: overflowed t1 behaves like t0 (waits for an FB). PEBS does not identify each
    sample's path, so the waiting 33 % are matched to the 42 % overflowed by size only.
22. t1 drops (sum < 1) only when the XQ is saturated (8 % at xq.full 92 %, 0 % at 62 % and 0 %).
23. Measured t0 wait: median 218 cycles (no pad), consistent with the 180 derived from the 8x1 FB budget and EMR's 150.
Caveat: PEBS slowed the runs by 3-5 % (19 % for t1 p32).

## 5. FB occupancy model: from hold time to U (Little's law)

### Symbols
| symbol | meaning | unit |
|---|---|---|
| F | FB entries (16) | entries |
| λ | lines per cycle through the core (one prefetch = one line) | lines/cycle |
| W | memory latency: L2 miss -> line back in L2 | cycles |
| r | extra time from L2 to L1 | cycles |
| p | fraction of prefetches that find the FB full at dispatch (overflow, = hwpf share) | - |
| w | time an overflowed request waits **outside** the FB (in the load buffer / ROB) before it gets an entry | cycles |
| H_d | time a request that got an entry at dispatch holds it | cycles |
| H_o | time an overflowed request holds its entry after it is granted | cycles |
| H | average time one line holds one FB entry: H = (1 - p) H_d + p H_o | entry-cycles per line |
| U | requests in flight at memory ("uncore buffer") | requests |

H is FB occupancy per line, not the time between lines (that is 1/λ). w does not enter H: a waiting request holds no entry.

### Equations
1. FB (Little's law): busy entries = λ H. When the FB is saturated, **λ = F / H**.
2. Memory (Little's law): **U = λ W**.
3. Combined: **U = (busy entries) W / H = F W / H** when saturated. W / H = lines each FB entry serves per memory latency.

### Hold times per instruction
| | H_d | H_o |
|---|---|---|
| t0 | W + r (until the line is in L1) | W + r - w (request already sent; holds until the line is in L1) |
| t1 | W (**assumed**: until the line is in L2) | h (**unknown**; the model's `t1late`) |

For t0, H = W + r - p w; w is capped by how long the ROB window lets a uop wait, so H stays large.
For t1, a short h lowers H, so λ and U rise. This is the current explanation of t1's higher U.

### Assumptions
1. Same λ at the FB and at memory: every prefetch sends exactly one memory request (hwpf + swpf = 1.000 measured) and
   takes exactly one FB entry, at dispatch or later. Breaks when prefetches are dropped (t1-only 8x1: sum 0.92) or
   lines hit in the LLC (6-13 % at 1x1); then the two stages carry different rates.
2. λ = F / H only when the FB is saturated (fb_full near 100 %). Otherwise λ is set by something else (XQ, ROB, issue
   rate); U = λ W still holds, but use the measured busy entries, not F.
3. Steady state (long ROI, no warm-up in the averages).
4. H_d = W for t1 is not measured. One constraint from t1 p32 (0.577 H_d + 0.423 H_o <= 16 x 13.7 = 219) fits both
   H_d = W with H_o <= 77, and H_d <= ~168 with H_o = W - w. Open.

### Toy check (`fbmodel.py`, F = 4, W = 40, r = 4, ROB 4 iterations, issue every 4 cycles, prefetch-only, h = 4)
| | p | H | busy entries | cycles/line | U = busy W / H | model U |
|---|---|---|---|---|---|---|
| t1 | 0.64 | 0.36 x 40 + 0.64 x 4 = 17 | 3.5 | 4.8 | 8.2 | 8.3 |
| t0 | 1.00 | 44 - w (avg w = 22) = 22 | 4.0 | 5.6 | 7.2 | 7.1 |

Trace an example: `python3 -c "import fbmodel as m; m.run('t1',pfonly=1,F=4,W=40,r=4,C=1000,R=4,c0=4,t1late=4,cycles=1030,warm=0,trace=(1000,1029))"`
(`trace=(from, to)` prints each event with the FB contents, `*` = entry granted after overflow; `pfonly=1` = no demand load.)
