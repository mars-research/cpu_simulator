# 12 independent pointer chases, interleaved (memory-level-parallelism probe)
#   rax = array of 12 chain pointers (L1-resident); one step of every chain per iteration
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
    dec rsi
    jnz 1b
