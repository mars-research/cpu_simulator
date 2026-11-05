# independent random loads over 4 GiB (memory-level-parallelism bound)
#   line = crc32c(0, i) & (2^26 - 1); rdi = buffer, rcx = i
    xor edx, edx
    crc32 rdx, rcx
    and edx, 0x3ffffff
    shl rdx, 6
    add r8, [rdi + rdx]
    inc rcx
    cmp rcx, rsi
    jne 1b
