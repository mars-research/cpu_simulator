# mix_t2x2: per iteration, t2 + t2, each to its own random line (crc32c(seed_j, i) & mask) over 4 GiB
#   separates the fill-buffer cost of each request type (uiCA memory model: fbServiceTime)
    xor r10d, r10d
    crc32 r10, rcx
    and r10d, 0x3ffffff
    shl r10, 6
    prefetcht2 [rdi + r10]
    mov edx, 0x9e3779b9
    crc32 rdx, rcx
    and edx, 0x3ffffff
    shl rdx, 6
    prefetcht2 [rdi + rdx]
    inc rcx
    cmp rcx, rsi
    jne 1b
