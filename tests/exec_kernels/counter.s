# a value passed through memory: read-modify-write, then a load of the same address (store-to-load forwarding)
#   rdi = counter, rsi = n; finally [rdi] = rax = 3n (+ initial value)
    add qword ptr [rdi], 3
    mov rax, [rdi]
    dec rsi
    jnz 1b
