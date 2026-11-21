"""Builds ground_truth.json from the raw perf logs in ../results/xmax/logs (Xeon Max 9462, 1 thread on 1 core,
HBM, prefetchers off; see ../results/xmax/README.md). Per test: cycles per line and the counters per line / per
cycle used to check the model. Lines = offcore_requests.data_rd (misses) or instructions / (8 + pad) (hot)."""
import json, os
L = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../results/xmax/logs')

def perf(tag):
    v = {}
    for l in open(f'{L}/{tag}.core.perf'):
        f = l.split(',')
        if l[0].isdigit() and len(f) > 2:
            v[f[2]] = float(f[0])
    return v

out = {'_doc': __doc__, 'core_only': {}, 'pfo_t0_misses': {}}
for pad in (0, 8, 16, 24, 32, 48):              # L1-resident prefetch-only t0 loop (BW_HOTLINES=256)
    v = perf(f'fe_t0_p{pad}_hot'); n = v['instructions'] / (8 + pad)
    out['core_only'][pad] = {'log': f'fe_t0_p{pad}_hot', 'alignmentOffset': {0: 48, 8: 48, 16: 16, 24: 8, 32: 32, 48: 24}[pad],
        'cycles_per_line': v['cycles'] / n, 'uops_per_line': v['uops_issued.any'] / n,
        'dsb_uops_per_line': v['idq.dsb_uops'] / n, 'mite_uops_per_line': v['idq.mite_uops'] / n, 'lsd_uops_per_line': v['lsd.uops'] / n}
# prefetch-only t0 over 1 GiB of HBM; occupancy run + hint-path run (same configuration, separate runs)
for pad, occ, hint in ((0, 'pfo_t0_1x1', 'pfohint_t0_1x1'), (16, 'pfo_t0_p16_1x1', 'pfohint_t0_p16_1x1')):
    a, b = perf(occ), perf(hint); n = a['offcore_requests.data_rd']
    out['pfo_t0_misses'][pad] = {'logs': [occ, hint], 'cycles_per_line': a['cycles'] / n,
        'U': a['offcore_requests_outstanding.data_rd'] / a['cycles'], 'W': a['offcore_requests_outstanding.data_rd'] / n,
        'fb_full_frac': a['l1d_pend_miss.fb_full'] / a['cycles'], 'xq_full_frac': a['xq.full_cycles'] / a['cycles'],
        'p_hwpf': b['l2_rqsts.hwpf_miss'] / b['sw_prefetch_access.t0'], 'swpf': b['l2_rqsts.swpf_miss'] / b['sw_prefetch_access.t0'],
        'l1_fills_per_prefetch': b['l1d.replacement'] / b['sw_prefetch_access.t0']}
r = [perf('pend_t0_p48_1x1'), perf('fbfull_t0_p48_1x1')]     # pad 48: two runs (pending / fb_full counter)
n = [x['offcore_requests.data_rd'] for x in r]
out['pfo_t0_misses'][48] = {'logs': ['pend_t0_p48_1x1', 'fbfull_t0_p48_1x1'],
    'cycles_per_line': sum(x['cycles'] / k for x, k in zip(r, n)) / 2,
    'U': sum(x['offcore_requests_outstanding.data_rd'] / x['cycles'] for x in r) / 2,
    'W': sum(x['offcore_requests_outstanding.data_rd'] / k for x, k in zip(r, n)) / 2,
    'fb_full_frac': r[1]['l1d_pend_miss.fb_full'] / r[1]['cycles'],
    'p_hwpf': sum(x['l2_rqsts.hwpf_miss'] / k for x, k in zip(r, n)) / 2,
    'l1d_pend_miss_pending_per_cycle': r[0]['l1d_pend_miss.pending'] / r[0]['cycles']}
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ground_truth.json'), 'w'), indent=1)
print(json.dumps({k: {p: round(v['cycles_per_line'], 2) for p, v in d.items()} for k, d in out.items() if k != '_doc'}))
