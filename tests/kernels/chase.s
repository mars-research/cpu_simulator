# pointer chase: one dependent load per iteration (latency bound)
#   rax = current node; each node holds the address of the next node
    mov rax, [rax]
    dec rsi
    jnz 1b
