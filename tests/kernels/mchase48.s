# 48 independent pointer chases, interleaved (memory-level-parallelism probe)
#   rax = array of 48 chain pointers (L1-resident); one step of every chain per iteration
    mov rdx, [rax + 0]
    mov rdx, [rdx]
    mov [rax + 0], rdx
    mov rdx, [rax + 8]
    mov rdx, [rdx]
    mov [rax + 8], rdx
    mov rdx, [rax + 16]
    mov rdx, [rdx]
    mov [rax + 16], rdx
    mov rdx, [rax + 24]
    mov rdx, [rdx]
    mov [rax + 24], rdx
    mov rdx, [rax + 32]
    mov rdx, [rdx]
    mov [rax + 32], rdx
    mov rdx, [rax + 40]
    mov rdx, [rdx]
    mov [rax + 40], rdx
    mov rdx, [rax + 48]
    mov rdx, [rdx]
    mov [rax + 48], rdx
    mov rdx, [rax + 56]
    mov rdx, [rdx]
    mov [rax + 56], rdx
    mov rdx, [rax + 64]
    mov rdx, [rdx]
    mov [rax + 64], rdx
    mov rdx, [rax + 72]
    mov rdx, [rdx]
    mov [rax + 72], rdx
    mov rdx, [rax + 80]
    mov rdx, [rdx]
    mov [rax + 80], rdx
    mov rdx, [rax + 88]
    mov rdx, [rdx]
    mov [rax + 88], rdx
    mov rdx, [rax + 96]
    mov rdx, [rdx]
    mov [rax + 96], rdx
    mov rdx, [rax + 104]
    mov rdx, [rdx]
    mov [rax + 104], rdx
    mov rdx, [rax + 112]
    mov rdx, [rdx]
    mov [rax + 112], rdx
    mov rdx, [rax + 120]
    mov rdx, [rdx]
    mov [rax + 120], rdx
    mov rdx, [rax + 128]
    mov rdx, [rdx]
    mov [rax + 128], rdx
    mov rdx, [rax + 136]
    mov rdx, [rdx]
    mov [rax + 136], rdx
    mov rdx, [rax + 144]
    mov rdx, [rdx]
    mov [rax + 144], rdx
    mov rdx, [rax + 152]
    mov rdx, [rdx]
    mov [rax + 152], rdx
    mov rdx, [rax + 160]
    mov rdx, [rdx]
    mov [rax + 160], rdx
    mov rdx, [rax + 168]
    mov rdx, [rdx]
    mov [rax + 168], rdx
    mov rdx, [rax + 176]
    mov rdx, [rdx]
    mov [rax + 176], rdx
    mov rdx, [rax + 184]
    mov rdx, [rdx]
    mov [rax + 184], rdx
    mov rdx, [rax + 192]
    mov rdx, [rdx]
    mov [rax + 192], rdx
    mov rdx, [rax + 200]
    mov rdx, [rdx]
    mov [rax + 200], rdx
    mov rdx, [rax + 208]
    mov rdx, [rdx]
    mov [rax + 208], rdx
    mov rdx, [rax + 216]
    mov rdx, [rdx]
    mov [rax + 216], rdx
    mov rdx, [rax + 224]
    mov rdx, [rdx]
    mov [rax + 224], rdx
    mov rdx, [rax + 232]
    mov rdx, [rdx]
    mov [rax + 232], rdx
    mov rdx, [rax + 240]
    mov rdx, [rdx]
    mov [rax + 240], rdx
    mov rdx, [rax + 248]
    mov rdx, [rdx]
    mov [rax + 248], rdx
    mov rdx, [rax + 256]
    mov rdx, [rdx]
    mov [rax + 256], rdx
    mov rdx, [rax + 264]
    mov rdx, [rdx]
    mov [rax + 264], rdx
    mov rdx, [rax + 272]
    mov rdx, [rdx]
    mov [rax + 272], rdx
    mov rdx, [rax + 280]
    mov rdx, [rdx]
    mov [rax + 280], rdx
    mov rdx, [rax + 288]
    mov rdx, [rdx]
    mov [rax + 288], rdx
    mov rdx, [rax + 296]
    mov rdx, [rdx]
    mov [rax + 296], rdx
    mov rdx, [rax + 304]
    mov rdx, [rdx]
    mov [rax + 304], rdx
    mov rdx, [rax + 312]
    mov rdx, [rdx]
    mov [rax + 312], rdx
    mov rdx, [rax + 320]
    mov rdx, [rdx]
    mov [rax + 320], rdx
    mov rdx, [rax + 328]
    mov rdx, [rdx]
    mov [rax + 328], rdx
    mov rdx, [rax + 336]
    mov rdx, [rdx]
    mov [rax + 336], rdx
    mov rdx, [rax + 344]
    mov rdx, [rdx]
    mov [rax + 344], rdx
    mov rdx, [rax + 352]
    mov rdx, [rdx]
    mov [rax + 352], rdx
    mov rdx, [rax + 360]
    mov rdx, [rdx]
    mov [rax + 360], rdx
    mov rdx, [rax + 368]
    mov rdx, [rdx]
    mov [rax + 368], rdx
    mov rdx, [rax + 376]
    mov rdx, [rdx]
    mov [rax + 376], rdx
    dec rsi
    jnz 1b
