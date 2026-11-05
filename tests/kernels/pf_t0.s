# software prefetcht0 of the line for iteration i+32, then a load of the line for iteration i
# (the find_batch pattern: prefetch at enqueue, load at pop); same hash as rand.s
    lea r9, [rcx + 32]
    xor edx, edx
    crc32 rdx, r9
    and edx, 0x3ffffff
    shl rdx, 6
    prefetcht0 [rdi + rdx]
    xor r10d, r10d
    crc32 r10, rcx
    and r10d, 0x3ffffff
    shl r10, 6
    add r8, [rdi + r10]
    inc rcx
    cmp rcx, rsi
    jne 1b
