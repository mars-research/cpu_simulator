#!/bin/bash
# usage: pebs.sh <inst> <pad> <tag> [ldlat] [period]  -- PEBS load-latency samples (dispatch -> completion) of the
# prefetch-only loop (BW_PFONLY=1), 1 core x 1 thread, same ROI as run_roi.sh. Writes logs/<tag>.lat (one weight per line)
D=$(dirname $(readlink -f $0)); inst=$1; pad=$2; tag=$3; th=${4:-3}; per=${5:-10007}
F=$D/logs/$tag; rm -f $F.c $F.a; mkfifo $F.c $F.a
sudo -n perf record -q -D -1 --control fifo:$F.c,$F.a -W -d -c $per -o $F.data \
  -e "{cpu/mem-loads-aux/,cpu/mem-loads,ldlat=$th/upp}" -- \
  env BW_PFONLY=1 PERF_CTL=$F.c PERF_ACK=$F.a $D/bw_roi -m 1gb -pattern n0a2t1 -freq 2.7 -inst $inst -lookahead 64 -pad $pad -mode r > $F.out
rm -f $F.c $F.a
sudo -n perf script -i $F.data -F event,ip,weight 2>/dev/null | awk '/mem-loads,ldlat/ && $NF !~ /^ffff/ {print $(NF-1)}' > $F.lat
sudo -n rm -f $F.data
grep ROI $F.out; echo "samples: $(wc -l < $F.lat)"
