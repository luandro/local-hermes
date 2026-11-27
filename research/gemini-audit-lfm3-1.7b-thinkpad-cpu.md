**Title:** ‎Gemini - acesso direto à IA do Google

**Source:** [https://gemini.google.com/share/3bb71c6a3a000465f953d2f7dc904fe6?social_preview=true&skid=0ba2ab5e-a083-4217-b62f-66feaf8cb355](https://gemini.google.com/share/3bb71c6a3a000465f953d2f7dc904fe6?social_preview=true&skid=0ba2ab5e-a083-4217-b62f-66feaf8cb355)

---

# Page Structure Map
```text
‎Gemini - acesso direto à IA do Google
├── Auditoria ao local-hermes e Otimização Avançada de Agentes Locais com LFM3-1.7B em ThinkPad Exclusivamente CPU
├── Auditoria ao Repositório local-hermes e Lista de Verificação Top 10
├── Otimização do Sistema Operativo, Kernel Linux e Memória no ThinkPad
│   ├── zswap vs. zram sob Pressão de Swap
│   ├── Proteção via cgroup v2 e o Conflito THP vs. mmap no llama.cpp v1.0+
│   └── Controlo Térmico, PL1/PL2, Afinidade CPU e glibc malloc no ThinkPad
├── Otimizações Avançadas no llama.cpp v1.0+ e Alternativas de Execução em CPU
│   ├── Quantização com Imatrix do Modelo Fundido: Q4\0 vs. IQ4\XS/IQ4\NL vs. Q4\K\M e QAD
│   ├── Armadilhas de Cache em Modelos Híbridos: Checkpoints, --slot-save-path e --kv-unified
│   └── Telemetria, Compilação (LTO/PGO/oneMKL) e Alternativas de Execução
├── Atualização do Hermes Agent (v0.14.0 → v0.23.4) e Ecossistema de Plugins
│   ├── Estado das Issues #46201, #121089 e #31600
│   ├── Micro-Compactação, Poda Proativa e Compactação Sem Chamadas LLM
│   ├── Middleware Hooks para Controlo de Raciocínio por Turno e Encaminhamento Local
│   └── Ecossistema de Plugins e Skills de Alto Impacto
├── Arquitetura Híbrida em Camadas, Latência de Ferramentas e Alternativas de Final de 2026
│   ├── Redução de Latência em Ferramentas e Unidade systemd Persistente
│   ├── Descodificação Especulativa (LFM3-1.7B-DSpark e n-gram) em CPU: Limiar de Rentabilidade
│   └── Alternativas de Modelos em Final de 2026 para o ThinkPad CPU
└── Plano de Execução Sequencial
```

---

## Auditoria ao local-hermes e Otimização Avançada de Agentes Locais com LFM3-1.7B em ThinkPad Exclusivamente CPU

O repositório indicado para auditoria (`https://github.com/luandro/local-hermes`) encontra-se inacessível à data deste relatório (privado, removido ou não indexado publicamente no GitHub). De igual modo, a página direta do repositório complementar `https://github.com/luandro/hermes-forgecode` não é acessível para inspeção de código-fonte, embora esteja indexada em diretórios públicos como uma *skill* do Hermes Agent (`v1.0.1`) vocacionada para delegação multiagente através da CLI Forgecode (`forge`/`sage`/`muse`). Por conseguinte, a auditoria remota ao código do `local-hermes` é substituída abaixo por uma lista de verificação determinística — executável localmente em poucos minutos — contra os dez vetores críticos de desempenho e estabilidade de uma pilha LFM3-1.7B + LoRA + `llama.cpp` + Hermes Agent.

Para além das correções de base, a investigação técnica ao ecossistema de final de 2026 (`llama.cpp` v1.0+ / `b12686` e Hermes Agent até `v0.23.4`, publicada a 23 de novembro de 2026) identifica quatro alavancas avançadas com impacto substancial num ThinkPad exclusivamente CPU (Intel de 8.ª geração com UHD 620, 4 núcleos / 8 *threads*, 23 GiB de RAM utilizável sob pressão de *swap*). A primeira é substituir qualquer combinação `zram`+ficheiro de *swap* por `zswap` com proteção `cgroup v2` (`MemoryMin`) e `--load-mode none` para ativar Transparent Hugepages (THP), eliminando simultaneamente a inversão LRU no *swap* e as falhas de TLB no pré-processamento (*prefill*). A segunda é corrigir dois comportamentos silenciosos do `llama-server` em arquiteturas híbridas convolução+SWA como o LFM3-1.7B: a perda total de reutilização de prefixo ao restaurar *slots* a partir de disco (`#25913`, `#28194`) e a eliminação prematura de *checkpoints* de contexto quando `--checkpoint-min-step` permanece no valor por omissão de 8.192 tokens. A terceira é atualizar o Hermes Agent da `v0.14.0` para a `v0.23.4`, adotando compactação determinística sem chamadas LLM (`llmslim` ou `hermes-vcc-engine`), truncagem agressiva de saídas de ferramentas (`Toolaria` e `hermes-sentinel`) e *middleware* `llm_request` para modular o orçamento de raciocínio por turno. A quarta é testar o *fork* `ik_llama.cpp` como candidato principal para acelerar o *prefill* em AVX2 — demonstrando, em contrapartida, por que razão a descodificação especulativa (`LFM3-1.7B-DSpark` e *n-gram*) tende a degradar a velocidade em CPU de baixa razão *prefill/decode*.

## Auditoria ao Repositório local-hermes e Lista de Verificação Top 10

Na ausência de acesso público ao repositório `luandro/local-hermes`, a confirmação do estado real da tua instalação faz-se inspecionando a unidade `systemd` do `llama-server`, o ficheiro `~/.hermes/config.yaml`, o diretório de modelos GGUF e os registos do servidor em modo de rastreio (`-lv 4`). Cada linha da tabela seguinte traduz um dos dez vetores críticos num critério objetivo de aprovação.

| # | Vetor de Auditoria | Comando / Ficheiro a Inspecionar | Critério de Aprovação (Estado Ótimo) | Modo de Falha se Desalinhado |
| --- | --- | --- | --- | --- |
| 1 | Fusão do LoRA (`LFM3-1.7B-hermes`) | `ps aux \| grep llama-server` e cabeçalho GGUF | Sem flag `--lora`; adaptador fundido em BF16 antes de quantizar | Sobrecarga de execução por falta de *kernels* LoRA rápidos e invalidação de cache KV |
| 2 | Quantização do GGUF | `ls -lh` ao modelo ativo | `Q4_K_M` (~1,10 GB) ou `Q4_0` (~1,0 GB) calibrado com *imatrix* | `Q8_0` (1,85 GB) ou `BF16` (3,47 GB) saturam a largura de banda DDR4 e reduzem `tg` |
| 3 | Estabilidade de prefixo e *checkpoints* | Registos `llama-server -lv 4` e `timings.cache_n` | Ausência de `forcing full prompt re-processing`; `-cms 256` a `512` e `-ctxcp 16` | Reprocessamento integral a cada ronda (numa modelação a 25 tok/s: 880 s em 4 rondas vs. 80 s quente) |
| 4 | Controlo de raciocínio do LFM3 | Flags `--reasoning-budget` / `--reasoning-format` | Limite explícito (ex.: 128–256 tokens) ou modulação por turno | Raciocínio ativo por omissão no LFM3 consome centenas de tokens adicionais por turno |
| 5 | Alinhamento de contexto e piso 64K | `-c` no `llama-server` vs. `model.context_length` | `-c 65536` alinhado com `model.context_length: 65536` e `threshold_tokens` baixo | Hermes recusa iniciar abaixo de 64.000 tokens ou entra em ciclos de erro de contexto |
| 6 | *Slots*, *threads* e *offload* GPU | `-np`, `-t`, `-tb`, `-ngl` | `-np 1`, `-t 4` (núcleos físicos), `-ngl 0` (CPU puro) | 8 *threads* degrada por contenção SMT/térmica; *offload* Vulkan na UHD 620 degrada geração |
| 7 | Tipo de cache KV e Flash Attention | `-ctk`, `-ctv`, `-fa` | `-ctk f16 -ctv f16 -fa on` (validar `q8_0` simétrico apenas se necessário) | Tipos mistos K/V podem causar *fallbacks* severos; `q8_0` acrescenta desquantização contínua |
| 8 | Higiene de *skills*, *toolsets* e versão Hermes | `hermes --version`, `hermes config get`, `/context` | Hermes `v0.23.4`, `agent.tool_use_enforcement: true`, *skills* não usadas desativadas | Versões antigas (`v0.14.0`) sofrem do bug `#46201` (~3K tokens de catálogo) e ferramentas fragmentadas |
| 9 | Proteção contra *swap* e THP | `swapon --show`, `cat /proc/<pid>/smaps_rollup` | Sem `zram`+`swapfile` simultâneos; `AnonHugePages` ativo e `MemoryMin` no `systemd` | Inversão LRU no *swap*, despejo de pesos para disco e falhas de TLB em páginas de 4 KiB |
| 10 | Compilação nativa e persistência | `llama-server --version` e `systemctl status` | Compilado com `-DGGML_NATIVE=ON` (AVX2/FMA), `v1.0+`, serviço persistente quente | Binários genéricos desperdiçam desempenho vetorial; arranques frios repetem *prefill* completo |

Para executar esta auditoria sem alterar a máquina, corre o bloco de diagnóstico seguinte e compara o resultado com os critérios acima:

```markdown
# 1. Versões e compilação ativa
llama-server --version
hermes --version

# 2. Topologia de núcleos físicos (SMT), swap e páginas enormes
lscpu -e=CPU,CORE,SOCKET,ONLINE
swapon --show
cat /sys/module/zswap/parameters/enabled 2>/dev/null || echo "zswap N/A"
cat /sys/kernel/mm/transparent_hugepage/enabled

# 3. Processo llama-server (flags, páginas enormes e swap)
PID=$(pgrep -f llama-server | head -n1)
[ -n "$PID" ] && tr '\0' ' ' < /proc/$PID/cmdline && echo ""
[ -n "$PID" ] && grep -E 'Rss:|AnonHugePages:|Swap:' /proc/$PID/smaps_rollup

# 4. Estado térmico/energético da CPU
cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference
cat /sys/devices/system/cpu/intel_pstate/no_turbo

# 5. Configuração crítica do Hermes
hermes config get model.context_length
hermes config get agent.tool_use_enforcement
hermes config get compression
```

## Otimização do Sistema Operativo, Kernel Linux e Memória no ThinkPad

Em inferência exclusivamente CPU, o ritmo de geração de tokens (`tg`) é limitado sobretudo pela largura de banda efetiva da RAM, enquanto o pré-processamento do *prompt* (`pp`) é dominado pela taxa sustentada de operações vetoriais AVX2/FMA sob os limites térmicos e energéticos do portátil. Num ThinkPad com 23 GiB de RAM utilizável (tipicamente uma topologia assimétrica de 24 GB físicos: 8 GB soldados mais um módulo SO-DIMM de 16 GB), entra em jogo o **Intel Flex Mode**. Neste regime, os primeiros 16 GB operam em canal duplo (*dual-channel*, ~34–38 GB/s estimados em DDR4-2400), enquanto os 8 GB superiores operam em canal único (*single-channel*), reduzindo a largura de banda para cerca de metade. Sob pressão de *swap* causada por navegadores e editores abertos em paralelo, manter o conjunto de trabalho ativo da pilha de IA (~2,32 GiB) estritamente residente em RAM física — impedindo que seja despejado para disco ou empurrado para ciclos de paginação — é determinante.

### zswap vs. zram sob Pressão de Swap

A recomendação generalizada de usar `zram` em qualquer portátil Linux foi seriamente contestada em 2026 após a análise publicada pelo programador de gestão de memória do kernel Linux Chris Down (*Debunking zswap and zram myths*, março de 2026). Numa máquina que dispõe de *swap* em SSD/NVMe e enfrenta pressão real de memória, **não deves executar `zram` em paralelo com *swap* em disco**. Quando o dispositivo `zram` tem prioridade mais alta, ocorre **inversão LRU**: páginas frias e raramente lidas (como memória de inicialização) enchem primeiro o `zram` e permanecem retidas em RAM, obrigando páginas mais recentes e ativas a serem escritas no SSD mais lento. Acresce que a memória ocupada pelo `zram` não é imputada a nenhum `cgroup`, quebrando o isolamento de memória entre serviços, e um sistema com `zram` exclusivo sem *swap* em disco comprime a *page cache* de ficheiros assim que o `zram` satura, podendo originar bloqueios prolongados antes de o *OOM killer* atuar (Down cita incidentes de 20 a 30 minutos na Cloudflare).

| Estratégia | Quando Escolher | Configuração Recomendada | Riscos e Mitigações |
| --- | --- | --- | --- |
| **`zswap` + *swap* em NVMe/SSD (Recomendado)** | ThinkPad com 23 GiB sob pressão de *swap* real ou necessidade de hibernação | Desativar `zram`; parâmetros de kernel `zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.accept_threshold_percent=90` (ou `lz4` para menor latência de CPU) + *shrinker* ativo | Evita inversão LRU despejando as páginas mais antigas para o SSD; num teste da Instagram citado por Down, `zswap` reduziu escritas em disco até 25% face à ausência de *swap*. Em Btrfs, usar subvolume `NODATACOW` dedicado. |
| **`zram` exclusivo (sem *swap* em disco)** | Apenas se removeres totalmente o *swapfile* /partição e o excesso de RAM for contido | `zswap.enabled=0`; `zram` a 50–100% da RAM com `lz4` (num *benchmark* comunitário, 2,03M IOPS vs. 820K em `zstd-1`) ou `zstd` (razão medida de ~3,58× num teste de desenvolvimento); `vm.page-cluster=0`; `vm.swappiness=100–150` | **Obrigatório** correr `systemd-oomd` ou `earlyoom` para evitar congelamentos prolongados quando o dispositivo encher. |

Em qualquer das opções, evita copiar receitas que elevam `vm.watermark_scale_factor=500`: em CPUs de portátil, esse ajuste acorda o `kswapd` precocemente e consome ciclos de CPU em segundo plano durante o *prefill*. Com `zswap`, mantém `vm.swappiness` entre `60` e `100` (testando isoladamente contra o valor padrão de `60`) e reduz `vm.page-cluster` para `0` (ou `1`), eliminando leituras antecipadas supérfluas de 8 páginas em falhas aleatórias.

### Proteção via cgroup v2 e o Conflito THP vs. mmap no llama.cpp v1.0+

Após o PR `#20834` do `llama.cpp`, as antigas opções `--no-mmap` e `--mlock` foram unificadas num único enumerado `--load-mode {none|mmap|mlock|dio}`, onde `mlock` implica sempre `mmap` (\`use\_mmap = load\_mode==MMAP |

| load\_mode==MLOCK`) [cite: 6, 28]. Esta mudança tem uma consequência direta em Linux: **Transparent Hugepages (THP, páginas de 2 MiB) aplicam-se apenas a memória anónima e `tmpfs`/`shmem`, não a mapeamentos `mmap`respaldados por ficheiro**. Em inferência CPU, manter os pesos num`mmap`de ficheiro força as multiplicações de matrizes a atravessar páginas de 4 KiB, provocando falhas de TLB contínuas que, segundo relatos técnicos na discussão`#29347`do`llama.cpp`, podem degradar o *prefill* em cerca de 30% mesmo após aquecimento. Adicionalmente, em versões com *repacking* de pesos CPU (`Q4\_0`), a combinação `repack + mmap` provocou duplicação de consumo de RAM em determinados modelos (`#19578\`).

Por sua vez, usar `--load-mode none` isoladamente coloca os pesos em memória anónima (beneficiando de THP), mas deixa-os vulneráveis a evicção para *swap* sob carga real (`#26110`). A solução robusta em Linux combina ambas as proteções:

1. Lançar o `llama-server` com **`--load-mode none`** (ou servir o GGUF a partir de `tmpfs` se privilegiares arranque imediato com suporte THP), confirmando depois que `AnonHugePages` em `/proc/<pid>/smaps_rollup` cobre ~1,1 GiB dos pesos.
2. Proteger a unidade `llama-server.service` via **cgroup v2** no `systemd` com `MemoryMin=2.5G` (reserva rígida que impede reclamação pelo kernel) e `MemoryLow=3.5G` (proteção de melhor esforço que direciona a reclamação primeiro para o navegador e outros processos). Evita reservar pools manuais `HugeTLB` dentro de um cgroup com limites rígidos sem contabilização explícita, pois falhas na imputação de fólios `HugeTLB` podem disparar `SIGBUS`.

### Controlo Térmico, PL1/PL2, Afinidade CPU e glibc malloc no ThinkPad

Os ThinkPads Intel de 8.ª geração (era UHD 620, como T480, T480s, X1 Carbon Gen 6 e T490 com CPUs i5-8265U/8350U ou i7-8550U/8650U) sofrem frequentemente em Linux de estrangulamento imposto pelo *firmware*: tetos térmicos conservadores de 80 °C na BIOS (contra ~97 °C em modelos equivalentes da Dell), queda rápida do limite de potência de PL2 (~44 W) para PL1 (15 W) após poucos segundos de *prefill* sustentado, e reduções adicionais disparadas pelo sensor de posição (*lap mode*).

Para recuperar frequência sustentada com segurança térmica e elétrica:

1. **Identificar a causa exata do limite**: Corre `sudo throttled --monitor`, `s-tui` ou `turbostat` durante 45 segundos de `llama-bench -p 2048`. Um indicador `PL1` ativo a temperaturas moderadas (70–82 °C) confirma estrangulamento energético; um indicador `Thermal` persistente acima de 95 °C exige limpeza de ventoinhas e substituição de pasta térmica antes de alterar limites de potência.
2. **Ativar `thermald --adaptive` ou `erpalma/throttled`**: O `thermald` (v2.0+) com `--adaptive` já interpreta nativamente as tabelas DPTF sem necessidade do utilitário legado `dptfxtract`. Se o portátil continuar preso a 15 W / 80 °C ligado à corrente, instala `erpalma/throttled` (`v0.13+`), que mantém perfis independentes `[AC]` e `[BATTERY]`, reaplica os registos após suspensão via D-Bus, suporta *undervolting* de CPU/cache (geralmente ainda desbloqueado na 8.ª geração Intel, ao contrário da 10.ª em diante) e oferece `HWP_Mode: True` para comutar `energy_performance_preference` para `performance` sob carga (o autor reportou +15% em núcleo único e +7% em multinúcleo no Geekbench 4). *Ressalva*: o `throttled` escreve diretamente em registos MSR/MCHBAR como *root* e chassis finos como o T480s não têm arrefecimento ativo nos VRMs, pelo que deves subir o PL1 de forma prudente (por exemplo 22–28 W) e vigiar temperaturas.
3. **Exclusão rigorosa de SMT (*Hyper-Threading*) e afinidade**: Num teste comunitário num i5-8265U (4 núcleos / 8 *threads*), usar 8 *threads* foi mais lento do que 4 ou 6 *threads*, confirmando o padrão recorrente de contenção de cache L3, sincronização e aquecimento em CPUs com SMT. Identifica os 4 CPUs lógicos de núcleos físicos distintos com `lscpu -e`, fixa `CPUAffinity=` no `systemd` e usa `-t 4` (comparando `-tb 4` vs. `-tb 6` com `llama-bench`, pois *prefill* e *decode* podem divergir). Em compilações com OpenMP, `OMP_PROC_BIND=close` e `GOMP_CPU_AFFINITY` evitam migração entre núcleos. Num portátil de *socket* único, `--numa` não traz vantagem.
4. **Limitação de arenas `glibc malloc`**: Por omissão, a `glibc` pode alocar até 8 arenas por *thread* lógica (64 arenas num sistema de 8 *threads*), aumentando a fragmentação RSS em daemons Python. O Hermes Agent já disponibiliza `malloc_arena_max` (padrão `4` em `cli-config.yaml.example`, aplicado apenas ao *gateway* e `hermes serve` em Linux/glibc sem limitar processos filhos, sendo sobreposto por um `MALLOC_ARENA_MAX` explícito). Exportar `MALLOC_ARENA_MAX=4` também no serviço do `llama-server` mantém o consumo estável sob pressão de memória.

## Otimizações Avançadas no llama.cpp v1.0+ e Alternativas de Execução em CPU

Afinar o `llama-server` sem considerar a topologia interna do `LiquidAI/LFM3-1.7B` leva facilmente a decisões contraproducentes. Segundo o `config.json` oficial, o modelo tem 1,73B de parâmetros em 24 camadas numa arquitetura tri-híbrida: **13 camadas de convolução curta com porta** (`conv_L_cache = 7`), **5 camadas de atenção de janela deslizante (SWA)** (`sliding_window = 256`) e apenas **6 camadas de atenção global** (`num_attention_heads = 16`, `num_key_value_heads = 4`, `head_dim = 128`, `hidden_size = 2048`, `intermediate_size = 7680`, `vocab_size = 128000`, `max_position_embeddings = 262144`, RoPE parcial a 50% nas camadas globais, *sandwich norm* e atenção com porta sigmóide por cabeça).

A partir destes parâmetros exatos, a pegada de cache KV e o peso de leitura por token descodificado calculam-se diretamente:

| Comprimento de Contexto (`-c`) | KV Atenção Global (6 cam., F16) | KV Atenção Global (6 cam., Q8\_0) | Cache SWA (5 cam., padrão vs. `--swa-full`) | Estado Conv (13 cam.) | Quota KV na Largura de Banda `tg` (vs. 1.100 MiB Q4\_K\_M) |
| --- | --- | --- | --- | --- | --- |
| **4.096 tokens** | 48,0 MiB | 25,5 MiB | 7,50 MiB (40,0 MiB) | 0,61 MiB | 4,4% (F16) / 2,4% (Q8\_0) |
| **16.384 tokens** | 192,0 MiB | 102,0 MiB | 7,50 MiB (160,0 MiB) | 0,61 MiB | 15,0% (F16) / 8,6% (Q8\_0) |
| **32.768 tokens** | 384,0 MiB | 204,0 MiB | 7,50 MiB (320,0 MiB) | 0,61 MiB | ~26,0% (F16) / ~15,7% (Q8\_0) |
| **65.536 tokens** | 768,0 MiB | 408,0 MiB | 7,50 MiB (640,0 MiB) | 0,61 MiB | 41,2% (F16) / 27,1% (Q8\_0) |
| **262.144 tokens** | 3.072,0 MiB | 1.632,0 MiB | 7,50 MiB (2.560,0 MiB) | 0,61 MiB | Dominado por KV |

Desta tabela emergem duas conclusões práticas. Primeira: como apenas 6 camadas mantêm cache KV global, alocar 64K de contexto em `F16` consome somente **768 MiB** (e 384 MiB a 32K). Guias genéricos recomendam `-ctk q8_0 -ctv q8_0`, mas a quantização de KV é uma funcionalidade de poupança de memória, não de velocidade: impõe desquantização a cada token descodificado e, em medições publicadas em *backends* acelerados, `F16` é mais rápido sempre que cabe em memória, ao passo que misturar tipos K e V distintos pode provocar *fallbacks* graves. Mantém **`-ctk f16 -ctv f16 -fa on`** como linha de base (verificando no teu CPU com `llama-bench -d 16384` se `q8_0` simétrico traz algum ganho de largura de banda a profundidades altas). Segunda: num *prefill* de 4.096 tokens, as 6 camadas de atenção global representam apenas **3,3%** dos FLOPs (412,3 GFLOPs face a 12.009,5 GFLOPs das projeções lineares); o verdadeiro multiplicador de latência em CPU não é o teto `-c` reservado, mas sim **evitar o reprocessamento repetido de milhares de tokens entre turnos**.

<iframe allow="xr-spatial-tracking; web-share" title="Visualizar" sandbox="allow-pointer-lock allow-popups allow-forms allow-popups-to-escape-sandbox allow-downloads allow-same-origin allow-scripts" src="https://2ydzft2eh671mviylmapoepygz2z1gtkcglyy8sr2vne5tqcjo-h1029127910.scf.usercontent.goog/gemini-code-immersive/shim.html?origin=https%3A%2F%2Fgemini.google.com&amp;cache=1"></iframe>

Como demonstra esta modelação, a pilha completa ocupa apenas cerca de 10% dos 23 GiB de RAM, mas uma falha recorrente de reutilização de cache em 4 rondas multi-ferramenta (4K→7K tokens a 25 tok/s) consome **880 segundos (14,7 minutos)** só em *prefill*, enquanto um prefixo de sistema aquecido aliado a saídas de ferramenta limitadas a 500 tokens reduz esse tempo para **80 segundos (1,3 minutos)** — uma poupança de **91%**.

### Quantização com Imatrix do Modelo Fundido: Q4\_0 vs. IQ4\_XS/IQ4\_NL vs. Q4\_K\_M e QAD

No `llama.cpp` v1.0.0 (lançado a 13 de novembro de 2026, que inclusive removeu o caminho legado `--lora-base`), a prática obrigatória é **fundir o adaptador `harness-hub/LFM3-1.7B-hermes` sobre o modelo `LiquidAI/LFM3-1.7B` em precisão total (BF16) antes de quantizar**. Aplicar `--lora` dinamicamente em tempo de execução evita *kernels* rápidos (com relatos comunitários de quedas até 5× no *prefill* em AMD Strix Halo e penalização também em CPU) e pode provocar invalidação da cache KV no `llama-server`. O fluxo correto consiste em executar `PeftModel.from_pretrained(...).merge_and_unload()` em BF16, converter para GGUF BF16 (`convert_hf_to_gguf.py --outtype bf16`), gerar uma matriz de importância (`llama-imatrix`) sobre um conjunto de calibração que espelhe as tuas sessões reais (esquemas JSON de *tool calling*, comandos de terminal, português e inglês) e quantizar via `llama-quantize --imatrix`.

Quanto ao formato de 4 bits num CPU AVX2 sem AVX-512, **`Q4_K_M` com *imatrix* (~1,10 GB)** continua a ser a escolha mais equilibrada porque mantém metade dos tensores `attention.wv` e `feed_forward.w2` em `Q6_K`, protegendo a precisão de argumentos JSON — sendo o ponto de partida recomendado pela própria Liquid AI. Em alternativa, **`Q4_0`** beneficia de *repacking* otimizado em CPU (`Q4_0_4_4` no `llama.cpp` e `Q4_0_R8` no `ik_llama.cpp`) e é o formato usado pela Liquid AI nos seus testes de velocidade de periferia, existindo inclusivamente um ficheiro oficial `LFM3-1.7B-QAD-Q4_0.gguf` com recuperação de precisão via *Quantization-Aware Distillation* (QAD). Contudo, não foi encontrada qualquer comparação independente que meça a perda ou preservação de *tool calling* entre `QAD-Q4_0` e `Q4_K_M`; mais importante ainda, ao fundires o LoRA `LFM3-1.7B-hermes` no *checkpoint* BF16 base, não herdas o ajuste QAD do ficheiro base — pelo que deves usar `llama-imatrix` sobre `Q4_K_M` e `Q4_0` e validar ambos em `llama-bench` e em tarefas reais do Hermes. Já **`IQ4_XS` e `IQ4_NL`** devem ser evitados no `llama.cpp` principal em AVX2, pois os formatos *IQ* sofrem lentidão acentuada no *prefill* de lotes grandes em AVX2 — só se tornam competitivos caso compile o PR `#27402` (que reportou +57% de *prefill* AVX2 em modelos IQ) ou migres para o `ik_llama.cpp`.

### Armadilhas de Cache em Modelos Híbridos: Checkpoints, --slot-save-path e --kv-unified

O `llama-server` combina três mecanismos frequentemente confundidos: reutilização de prefixo no *slot* (`--cache-prompt`), *checkpoints* parciais em RAM para estados não truncáveis (`--ctx-checkpoints`, `--checkpoint-min-step`, limitados por `--cache-ram`), e gravação/restauro de *slots* em disco (`--slot-save-path` + `POST /slots/{id}?action=save|restore`). Como o LFM3-1.7B inclui **13 camadas convolucionais e 5 camadas SWA**, não consegue simplesmente truncar a cache KV para trás; para retomar qualquer prefixo, o servidor tem de possuir um *checkpoint* anterior ao ponto de divergência. Daqui decorrem três armadilhas concretas:

1. **Falha total de reutilização com `--slot-save-path` em modelos híbridos/SWA (`#25913`, `#28194`)**: Na versão atual do `llama.cpp`, `POST /slots/0?action=save` grava apenas os tokens e células KV, **sem serializar `slot->prompt.checkpoints`**, enquanto `action=restore` limpa a lista de *checkpoints*. O servidor devolve HTTP 200 (`n_restored == n_saved`), mas no turno seguinte entra no caminho de *checkpoint*, não encontra nenhum, apaga o estado e reprocessa 100% do *prompt* (`cache_n == 0`). Ao contrário de modelos SWA puros como o Gemma — onde `--swa-full` serve de contorno —, num modelo híbrido com camadas recorrentes/convolucionais `--swa-full` não elimina a exigência de *checkpoint* para o estado recorrente. Assim, em vez de dependeres de gravação em disco entre reinícios, mantém o `llama-server` permanentemente ativo via `systemd` com um pedido de pré-aquecimento após o arranque (ou avalia a série de *patches* proposta na issue `#28194`, que introduz `--slot-save-checkpoints N`). Quando essa persistência funcionar, tem presente que alterar `-fa` ou reduzir `-c` invalida o ficheiro guardado.
2. **Evicção silenciosa de *checkpoints* devido a `-cms 8192`**: Por omissão, `--checkpoint-min-step` está fixado em 8.192 tokens, fazendo o servidor descartar *checkpoints* de turnos mais curtos (`erasing context checkpoint too close to an earlier one`) e desencadeando reprocessamento integral — registado apenas a nível `TRACE` (`-lv 4`), invisível no `-lv 3` padrão. Define **`-cms 256` (ou `512`), `-ctxcp 16` e `-cram 1024`**. Ao contrário de modelos híbridos de 27B–35B onde cada *checkpoint* consome 62–214 MiB, no LFM3-1.7B o estado parcial SWA + convolução (`LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY`) é estimado a partir do `config.json` em apenas ~8,1 MiB (7,50 MiB SWA + 0,61 MiB conv), pelo que 16 *checkpoints* ocupam cerca de ~130 MiB de RAM. Evita as *flags* obsoletas ou inexistentes `--checkpoint-every-n-tokens` (removida no PR `#22929`) e `--cache-disk` (apenas proposta na issue `#20697`).
3. **`-np 1` vs. `--kv-unified`**: Em CPU de portátil, **`-np 1`** é a configuração recomendada porque dois *slots* concorrentes disputam a mesma largura de banda e degradam ambos os tempos de resposta. Reserva `-np 2 --kv-unified` apenas se precisares mesmo de atender Telegram e *cron* /CLI em paralelo partilhando o total de `-c`, vigiando `timings.cache_n` devido ao bug `#30961` (com correção proposta no PR `#30997`) onde o restauro de cache sob `--kv-unified` podia falhar antes de limpar *slots* inativos.

### Telemetria, Compilação (LTO/PGO/oneMKL) e Alternativas de Execução

Para observabilidade rigorosa, inicia o servidor com `--metrics` (métricas Prometheus em `GET /metrics`) e `--slots` (`GET /slots` para inspecionar tokens em cache). Em pedidos em *streaming*, passar `"return_progress": true` devolve frames `prompt_progress` com `total`, `cache`, `processed` e `time_ms`, permitindo confirmar em tempo real que o rácio `cache / total` se mantém elevado e intercetar imediatamente qualquer regressão de prefixo. Em diagnóstico pontual, `--log-prompts-dir` grava os *prompts* literais em disco para comparação byte a byte (por exemplo com `prompt-pillar`).

No plano de compilação e de motores alternativos em final de 2026, a evidência recolhida aponta para a seguinte hierarquia de decisões:

| Motor / Opção de Compilação | Veredicto no ThinkPad (AVX2 + UHD 620) | Evidência e Ressalvas |
| --- | --- | --- |
| **`llama.cpp` v1.0+ nativo (`-DGGML_NATIVE=ON`)** | **Base estável recomendada** | Guias de implantação apontam ganhos típicos de 10–20% ao compilar nativamente face a binários genéricos. Mantém **`-DGGML_BLAS=OFF`** por omissão: a `llamafile/sgemm` embutida superou OpenBLAS em até ~30% no *prefill* em testes comunitários, e ativar `GGML_BLAS` tende a sobrepor-se a ela em lotes grandes. Evita LTO agressivo em *backends* dinâmicos em CPUs mais antigos pelo risco de `SIGILL`; não foram encontrados *benchmarks* independentes de PGO em AVX2 para `llama.cpp`, devendo qualquer ajuste de compilador ser validado em `llama-bench`. |
| **Intel `oneMKL` via `-DGGML_BLAS=ON`** | **Testar apenas com `llama-bench`; não assumir ganho** | A integração CBLAS acelera exclusivamente *prefill* (lotes > 32) e não altera `tg`. Os ganhos de `oneMKL` citados em literatura de servidores (ex.: Sapphire Rapids Xeon) ou em SYCL XMX não se traduzem automaticamente para AVX2 de portátil, onde `oneMKL` substitui a `llamafile/sgemm`. Só adotes se superar `GGML_BLAS=OFF` em `llama-bench -p 512,2048`. |
| **`ik_llama.cpp` (PR `#2782` LFM3 + PR `#2472` AVX2)** | **Principal candidato para salto de *prefill*** | Suporta LFM3 denso (PR `#2782`) e GEMM `Q4_0_R8` otimizado para AVX2 puro (PR `#2472`, setembro de 2026). Não existem *benchmarks* independentes de LFM3-1.7B neste fork, mas um teste comunitário num i5-8500T (mesma 8.ª geração AVX2, DDR4-2666, em Gemma 4 24B-A4B QAT) registou **74,68 vs. 37,94 tok/s (+97%) em *prefill*** e **13,51 vs. 11,32 tok/s (+19%) em *decode***. *Ressalva*: fork divergente desde agosto de 2024; exige teste explícito de `--jinja`, *tool calling* e `--reasoning-budget` com o Hermes. |
| **Vulkan / SYCL na Intel UHD 620** | **Descartar (`-ngl 0`)** | Num teste comunitário num portátil i5-8265U + UHD 620 (Fedora 44), o *offload* Vulkan num modelo de 36 camadas caiu para **3,88 tok/s** (parcial, 8/36) e **1,90 tok/s** (total, 36/36), ficando muito abaixo da execução em CPU puro (onde o LFM3-1.7B atingiu **15,25 tok/s**). Embora outro relato sugira ganhos pontuais de *prefill* na UHD 620 à custa de geração mais lenta, a falta de comutação híbrida prefill-GPU/decode-CPU torna o CPU puro superior no saldo global. SYCL é consistentemente mais lento que Vulkan em GPUs Intel e exige a pilha oneAPI. |
| **OpenVINO GenAI (`2026.5.0` nightly)** | **Acompanhar; ainda prematuro para o agente** | Suporte inicial a LFM3/LFM3-VL/LFM3-Omni chegou via `optimum-intel#2087` e OpenVINO `2026.5.0`, mas o *backend* OpenVINO no `llama.cpp` tem limitações conhecidas em formas dinâmicas e arquiteturas híbridas/SSM em modo servidor, e o OVMS obriga a converter o modelo fundido para IR perdendo a ergonomia GGUF e controlo de raciocínio do `llama-server`. |
| **`zml/llmd`** | **Não indicado para CPU-only AVX2** | Documentado pela Liquid AI para aceleradores (Metal, CUDA, ROCm, Intel oneAPI, TPU) com pesos FP8; sem evidência de vantagem em inferência 4-bit exclusivamente CPU. |

## Atualização do Hermes Agent (v0.14.0 → v0.23.4) e Ecossistema de Plugins

Atualizar da `v0.14.0` para a **`v0.23.4`** (23 de novembro de 2026, construída sobre a `v0.23.0` *"The Hearth Release"* de 5 de novembro de 2026) resolve gargalos centrais de memória e latência. A arquitetura passou para **um único *gateway* por máquina** que serve CLI, TUI, Desktop/Webapp, Telegram e *cron* na mesma fila e histórico — eliminando processos Python duplicados em RAM. Na `v0.23.0`, execuções *one-shot* passaram a enviar **menos ~1.570 tokens de prompt por chamada**, tarefas *cron* partilham cache de prefixo, as *skills* integradas baixaram de 58 para 44 e as 10 ferramentas legadas `browser_*` foram consolidadas numa única `browser_exec` (`#143146`), enxugando o cabeçalho de esquemas JSON. Foram igualmente corrigidos bloqueios críticos no Telegram, incluindo conclusões em segundo plano presas em fila infinita (`#139026`), saída permanente com código 75 após reciclagem de PID (`#139855`) e recuperação de mensagens retidas após esgotamento de contexto (`#139783`).

Ao atualizar, usa `hermes update` ou o instalador oficial (o pacote PyPI pode carregar versões antigas sem o carregador portável de *plugins* `v1.0.0`), tem em conta que a linha `v0.22+` gere Python 3.14, e revê quaisquer *prompts* ou listas que ainda referenciem `browser_navigate`/`browser_click` (agora `browser_exec`) ou `hermes mcp serve` (agora `hermes mcp-serve`).

### Estado das Issues #46201, #121089 e #31600

| Issue | Problema Original | Estado em Novembro de 2026 | Ação Obrigatória |
| --- | --- | --- | --- |
| **`#46201`** | *Skills* desativadas por plataforma continuavam injetadas no *system prompt* | **CORRIGIDO** (desde `v2026.6.19`, unificando todos os pontos de resolução) | Atualizar para `v0.23.4` e desativar categorias supérfluas via `hermes skills config` ou `hermes skills opt-out` (o catálogo completo custa ~3K tokens). |
| **`#121089`** | `platform_toolsets` e `disabled_toolsets` ignorados pelo *gateway* no Telegram (`terminal` ficava ativo) | **AINDA ABERTA** (reportada na `v0.21.4` e ainda com estado *Open*) | Não dependas só de `platform_toolsets.telegram` para bloquear `terminal`. Testa `tools.disabled_functions` (introduzido a partir de `#31375`/`#69675`) e reforça com um *hook* `pre_tool_call` ou *middleware* `llm_request`. |
| **`#31600`** | Piso `MINIMUM_CONTEXT_LENGTH = 64_000` impede arranque abaixo de 64K e trava auto-compressão | **FECHADA COMO *NOT PLANNED*** (piso de 64.000 tokens mantido) | Serve `-c 65536` no `llama-server` (768 MiB em F16 no LFM3-1.7B), fixa `model.context_length: 65536` e limita a janela ativa com `compression.threshold_tokens: 16384`. |

### Micro-Compactação, Poda Proativa e Compactação Sem Chamadas LLM

Um erro frequente após atualizar o Hermes é ativar `compression.micro_compact: true` esperando reduzir latência. A documentação oficial alerta expressamente que **a micro-compactação efetua uma chamada LLM no final de cada turno e reescreve o histórico já enviado, invalidando o prefixo da cache de prompt em todos os turnos**. Num portátil exclusivamente CPU, destruir a cache de prefixo turno após turno é altamente penalizador.

Em vez disso, combina quatro alavancas sem chamadas LLM:

1. **Motor de contexto determinístico (`llmslim` ou `hermes-vcc-engine`)**: O *plugin* `llmslim` (`context.engine: llmslim`) comprime localmente turnos antigos e saídas de ferramentas de forma determinística **sem reescrever mensagens já compactadas no início do histórico, preservando a cache de prefixo**. Para fluxos dominados por terminal, o `hermes-vcc-engine` constrói resumos em milissegundos preservando comandos exatos, códigos de saída e erros sem invocar qualquer modelo.
2. **Teto absoluto baixo e poda proativa**: Configura `compression.tail_mode: lean` (cauda compacta em vez dos 20% do modo `legacy`), `compression.threshold_tokens: 16384` (mantendo o trabalho ativo antes de a cache KV encarecer o *decode*), ativa `compression.proactive_prune_tokens` para podar resultados antigos de ferramentas sem LLM, e liga `compression.progress_notices: true` para visibilidade no Telegram.
3. **Resiliência em *fallback***: Mantém `compression.abort_on_summary_failure: false` para beneficiar da escada de arrefecimento (`60 s → 300 s → 900 s`) e do *fallback* determinístico `DETERMINISTIC_SUMMARY_ROUTE` caso um resumo auxiliar bloqueie.
4. **Cautela com `Tool Search` (`tools.tool_search`)**: O *Tool Search* nativo (BM25 com `tool_search`/`tool_describe`/`tool_call`) só compensa com catálogos MCP grandes (tipicamente 15+ ferramentas). Em conjuntos curtos, acrescenta 1–2 rondas extras na primeira utilização, não aproveita a cache do *system prompt* e tem taxa de falha de recuperação relevante mesmo em modelos de fronteira (a documentação cita 49% → 74% no Opus 4, logo ~26% de falha, piorando em modelos de 1,7B). Prefere podar ferramentas individuais com `tools.disabled_functions`.

### Middleware Hooks para Controlo de Raciocínio por Turno e Encaminhamento Local

A interface oficial de **Middleware do Hermes** (`llm_request`, `llm_execution`, `tool_request`, `tool_execution`) combinada com os *hooks* de transformação (`transform_tool_result`, `transform_terminal_output`, `pre_tool_call`) permite modular o `LFM3-1.7B` turno a turno. Como o raciocínio do LFM3 vem ativo por omissão e a Liquid AI avisa que desativá-lo globalmente com `enable_thinking=False` pode causar inconsistências em tarefas exigentes, um *plugin* mínimo em `llm_request` pode injetar `extra_body` seletivamente sem tocar no *system prompt* (mantendo a estabilidade de bytes da cache):

```markdown
# ~/.hermes/plugins/local-cpu-router/plugin.py
import re

SIMPLE_PATTERNS = re.compile(
    r"^(olá|oi|bom dia|boa tarde|obrigado|ok|sim|não|status|ping|horas)\b",
    re.IGNORECASE,
)

def register(ctx):
    ctx.register_middleware("llm_request", shape_local_lfm3_request)

def shape_local_lfm3_request(**kwargs):
    request = dict(kwargs["request"])
    messages = request.get("messages") or []
    extra_body = dict(request.get("extra_body") or {})
    extra_body.setdefault("return_progress", True)

    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    text = last_user if isinstance(last_user, str) else ""

    if len(text.strip()) < 45 and SIMPLE_PATTERNS.search(text.strip()):
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        extra_body["reasoning_budget"] = 0
        reason = "fast-path: short conversational turn"
    else:
        extra_body["reasoning_budget"] = 192
        extra_body["reasoning_budget_grace_tokens"] = 24
        reason = "bounded reasoning for local CPU agent"

    request["extra_body"] = extra_body
    return {"request": request, "source": "local-cpu-router", "reason": reason}
```

Confirma com `llama-server --help` os nomes exatos dos parâmetros JSON suportados pela tua compilação (`reasoning_budget`, `reasoning_budget_grace_tokens`, `chat_template_kwargs`), uma vez que o controlo de orçamento suave/graça entrou em PRs recentes (`#25961`).

### Ecossistema de Plugins e Skills de Alto Impacto

Para além do `llmslim` e do `hermes-vcc-engine`, cinco extensões do catálogo do Hermes respondem diretamente às restrições de um agente local em CPU:

- **`luandro/hermes-forgecode`**: Delega tarefas complexas de engenharia de software à CLI Forgecode (`forge`/`sage`/`muse`). Como a própria Liquid AI desaconselha o LFM3-1.7B para codificação agêntica complexa, manter o Hermes local como orquestrador rápido no Telegram e encaminhar trabalho de código pesado para o Forgecode resolve a principal fronteira de capacidade do modelo de 1,7B.
- **`Toolaria`**: Interceta resultados extensos de `web_extract` ou MCP em `transform_tool_result`, armazena o corpo completo num repositório local indexado por SHA-256 e entrega ao modelo um excerto compacto com um identificador `tla:<hash>` (num teste de demonstração do autor, reduziu 171.874 caracteres para 1.733 — 99× menos — permitindo passar o volume total à ferramenta seguinte sem o injetar no contexto).
- **`hermes-sentinel`** (requer Hermes `>=0.22.6`): Trunca saídas de ferramentas a um máximo de **4.000 caracteres e 100 linhas** antes de atingirem o modelo e bloqueia a 3.ª invocação consecutiva idêntica em `pre_tool_call`, cortando ciclos infinitos típicos de modelos pequenos. Como alternativa centrada em filtragem de saídas com recuperação linha a linha, existe o `hermes-detox`.
- **`hermes-helm`**: Expõe `/helm` no Telegram para inspecionar o custo exato em tokens de cada MCP, *skill* e *toolset*, alterná-los por toque e reenviar turnos perdidos após reinícios.
- **`hermes-rescue` e `polytropos-router`**: O `hermes-rescue` diagnostica falhas de API localmente sem chamadas LLM e cria *snapshots* antes de alterações arriscadas a `SOUL.md`, `memories/` e `state.db`; o `polytropos-router` (adicionado ao catálogo na `v0.23.3`) facilita encaminhamento multi-modelo. Evita, pelo contrário, o `prompt-optimizer`, cujo modo automático adiciona uma chamada LLM extra a todas as mensagens com 5+ palavras.

## Arquitetura Híbrida em Camadas, Latência de Ferramentas e Alternativas de Final de 2026

Mesmo com kernel e `llama-server` afinados, uma única saída não filtrada de `terminal` ou `web_extract` pode arruinar a sessão: o limite interno por omissão de `terminal_tool.py` trunca apenas aos **50 KB** (40% início / 60% fim) — cerca de 12.500–16.000 tokens, que a 25 tok/s de *prefill* representam 8 a 10 minutos de espera num só turno. A arquitetura global deve funcionar como um funil em três camadas onde cada nível protege a CPU local.

<iframe allow="xr-spatial-tracking; web-share" title="Visualizar" sandbox="allow-pointer-lock allow-popups allow-forms allow-popups-to-escape-sandbox allow-downloads allow-same-origin allow-scripts" src="https://4vayld41l89hmtih7ecvh2edc7x4v1ogdpsnqq73vwhsd2odwa-h1029127910.scf.usercontent.goog/gemini-code-immersive/shim.html?origin=https%3A%2F%2Fgemini.google.com&amp;cache=1"></iframe>

### Redução de Latência em Ferramentas e Unidade systemd Persistente

Para cortar latência fora da inferência pura, combina `Toolaria` + `hermes-sentinel` (mantendo `tool_output.max_line_length` no novo padrão de `10000` da issue `#16520` para não truncar linhas longas de configuração ou JSON), ativa o motor web integrado **`hermes config set web.backend keenable`** (sem chave nem *plugin*, com parâmetros `max_results` e `snippet_max_length`; nota que `AUXILIARY_WEB_EXTRACT_*` ficou obsoleto porque `web_extract` deixou de chamar um LLM auxiliar), e muda o isolamento de terminal para **`hermes config set terminal.backend bubblewrap`** (`v0.22+`), evitando a latência de arranque de contentores Docker.

A unidade `systemd` seguinte consolida todas as otimizações de sistema operativo e `llama-server` para manter o servidor permanentemente quente (ajusta `CPUAffinity` aos 4 núcleos físicos do teu `lscpu -e`):

```markdown
# /etc/systemd/system/llama-server.service
[Unit]
Description=llama-server LFM3-1.7B Merged Hermes (CPU-Only ThinkPad)
After=network-online.target

[Service]
Type=simple
User=luandro
# Ajustar CPUAffinity aos 4 núcleos físicos reais de: lscpu -e=CPU,CORE
CPUAffinity=0 1 2 3
Nice=-10
MemoryAccounting=true
MemoryMin=2.5G
MemoryLow=3.5G
Environment="MALLOC_ARENA_MAX=4"
Environment="OMP_PROC_BIND=close"
ExecStart=/usr/local/bin/llama-server \
  -m /opt/models/LFM3-1.7B-hermes-merged-imatrix-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 0 -t 4 -tb 4 \
  -c 65536 -np 1 \
  -b 2048 -ub 512 \
  -fa on -ctk f16 -ctv f16 \
  --load-mode none \
  --jinja \
  --cache-prompt --cache-ram 1024 \
  --ctx-checkpoints 16 --checkpoint-min-step 256 \
  --reasoning-format deepseek \
  --reasoning-budget 256 \
  --temp 0.1 --top-k 50 --repeat-penalty 1.1 \
  --metrics --slots
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Evita ativar `--sleep-idle-seconds` se precisares de resposta imediata no Telegram após períodos ociosos (usa-o apenas se preferires libertar os ~2 GiB de RAM durante longas pausas). Para eliminar o atraso frio após reinícios do portátil, acrescenta um *script* `ExecStartPost` que dispare uma chamada `/v1/chat/completions` com `max_tokens: 1` usando o *system prompt* estático do Hermes.

### Descodificação Especulativa (LFM3-1.7B-DSpark e n-gram) em CPU: Limiar de Rentabilidade

Um modelo rascunho dedicado ao LFM3-1.7B — **`rasyosef/LFM3-1.7B-DSpark-GGUF`** (~0,3B parâmetros, bloco `K=8`, usado com `--spec-type draft-dspark`) — foi reportado pelo seu autor (issue `#1362` em `vllm-project/speculators`) com ganhos de **2,63× em código e até 3,14× em matemática** (média de 2,88×) em aceleração GPU, atingindo comprimentos médios de aceitação de 2,83 tokens no geral, 3,24 em código e 4,10 em matemática (incluindo o token bónus).

Transpor esses números para um CPU de portátil, porém, falha pela física da razão *prefill/decode*. Em GPU, verificar um lote de 9 tokens custa quase o mesmo tempo que descodificar 1 token; em CPU de portátil, a verificação de um lote pequeno (`pp_batch9`) é apenas **1,5× a 2,5×** mais rápida do que `tg`. O multiplicador líquido de um ciclo DSpark (1 passagem do rascunho de custo relativo $c_{\text{draft}}$ + 1 verificação de $K+1$ tokens) segue a relação:

$$
\text{Aceleração} = \frac{\alpha_{\text{len}}}{c_{\text{draft}} + \frac{K + 1}{r}}
$$

onde $\alpha_{\text{len}}$ é o comprimento médio de aceitação por ciclo, $c_{\text{draft}}$ é ~0,22× (Q4) ou ~0,35× (Q8) de um passo de *decode* do modelo principal, e $r = \text{pp}_{\text{lote}} / \text{tg}$. Analogamente, para especulação *n-gram* sem modelo rascunho (`--spec-type ngram-simple` ou `ngram-mod`, exigindo `llama.cpp >= b11387`, que corrigiu a regressão de rejeição de rascunhos curtos a temperatura > 0), o ganho com comprimento $m$ e taxa de aceitação $a$ é $\frac{a \cdot m + 1}{m + 1} \cdot r$.

<iframe allow="xr-spatial-tracking; web-share" title="Visualizar" sandbox="allow-pointer-lock allow-popups allow-forms allow-popups-to-escape-sandbox allow-downloads allow-same-origin allow-scripts" src="https://0tqrax2219lfz3ct4vpo8zd0y7jhro4l3wg27y6jj1ebdaxsi9-h1029127910.scf.usercontent.goog/gemini-code-immersive/shim.html?origin=https%3A%2F%2Fgemini.google.com&amp;cache=1"></iframe>

Na prática, **mantém a especulação desligada por omissão no ThinkPad**. Se quiseres experimentar `LFM3-1.7B-DSpark-GGUF` (em `Q4_K_M`, evitando `Q8_0`), limita `--spec-draft-n-max` a `3` ou `4` — tendo presente que o rascunho foi treinado no `LFM3-1.7B` base, pelo que a fusão do LoRA `LFM3-1.7B-hermes` pode reduzir a taxa de aceitação. De forma semelhante, `ngram-simple` (`m=4`) compensa apenas em edições ou estruturas JSON muito repetitivas e penaliza diálogo livre. Valida sempre no teu portátil com `SPEED-Bench` (`tools/server/bench/speed-bench`) sobre *prompts* reais do Hermes.

### Alternativas de Modelos em Final de 2026 para o ThinkPad CPU

A oferta de modelos locais em novembro de 2026 inclui concorrentes relevantes — **Gemma 4 E2B/E4B**, **Qwen3.5-4B/9B**, **MiniCPM5-2B** e o recém-publicado **Inkling 2 Mini** (20 de novembro de 2026). De acordo com o conjunto de *benchmarks* publicado pela Liquid AI (em BF16 a contexto total), o **LFM3-1.7B** lidera cinco das seis provas de agentes e utilização de ferramentas frente a modelos até cinco vezes maiores, mantendo simultaneamente a menor cache KV a 128K (1,6 GB vs. 4,3 GB no Qwen3.5-4B e 5,6 GB no MiniCPM5-2B).

<iframe allow="xr-spatial-tracking; web-share" title="Visualizar" sandbox="allow-pointer-lock allow-popups allow-forms allow-popups-to-escape-sandbox allow-downloads allow-same-origin allow-scripts" src="https://34hvrp13sx2h6k2w10nx3rvf77ingbhs63v5oj127fzzroaoac-h1029127910.scf.usercontent.goog/gemini-code-immersive/shim.html?origin=https%3A%2F%2Fgemini.google.com&amp;cache=1"></iframe>

Cruzando estes dados (tendo em conta que os números da tabela acima provêm da avaliação publicada pelo fabricante) com a realidade física do ThinkPad, o papel de cada alternativa fica claramente delimitado. O **Gemma 4 E2B (QAT + MTP)** aproximou-se da velocidade do LFM3-1.7B num teste comunitário num i5-8265U (**14,55 tok/s** com 3 tokens de rascunho MTP no `llama.cpp b12208` contra **15,25 tok/s** e 1,84 GiB de RSS pico no LFM3-1.7B) e oferece visão e áudio nativos, mas o seu contexto *on-device* de 32K fica abaixo do piso de 64K do Hermes e a sua família pontua muito abaixo em *tool calling*. Para capacidades multimodais no Telegram mantendo a mesma espinha dorsal leve, a opção mais coerente é a própria família **LFM3-VL-2B / LFM3-Audio-2B / LFM3-Omni-2B**, já suportada em Linux. O **Qwen3.5-4B** (2,71 GB em 4 bits) justifica-se apenas como modelo auxiliar pontual quando dependes intensivamente de servidores MCP (onde supera o LFM3-1.7B em MCP-Atlas: 41,37 vs. 32,57), pagando mais do dobro do custo de largura de banda por token. Por fim, o **Inkling 2 Mini** (Thinking Machines Lab, 26B totais / 3B ativos com *loop* de 24→48 passagens, 48,2% em DeepSWE v1.1 e superior ao Qwen3.8-27B em 32 de 48 provas) bem como MoEs de 17B–24B (como o Ling-mini-2.0) exigem uma pegada de pesos total (na ordem dos ~14–17 GB em 4 bits para 26B parâmetros) incompatível com o canal duplo de 16 GB do Flex Mode sob pressão de *swap* — sendo ideais apenas como destino de escalação na Camada 2 num servidor LAN via Tailscale.

## Plano de Execução Sequencial

Para validar cada ganho de forma isolada e evitar regressões silenciosas, aplica as melhorias por esta ordem:

1. **Estabilizar memória, THP e limites térmicos do SO (30–45 min)**: Remover a coexistência `zram`+`swapfile` migrando para `zswap` (`zstd` ou `lz4`), configurar `CPUAffinity` nos 4 núcleos físicos + `MemoryMin=2.5G` + `--load-mode none` no `systemd` (confirmando `AnonHugePages` em `smaps_rollup`), e diagnosticar/corrigir PL1/PL2 com `thermald --adaptive` ou `erpalma/throttled`.
2. **Corrigir cache híbrida e quantizar o modelo fundido com *imatrix* (45–60 min)**: Fundir `harness-hub/LFM3-1.7B-hermes` em BF16, quantizar para `Q4_K_M` (comparando com `Q4_0`) com `llama-imatrix`, aplicar `-cms 256 -ctxcp 16 -cram 1024 -c 65536 -np 1 -ctk f16 -ctv f16 --metrics --slots`, e validar com `-lv 4` que `forcing full prompt re-processing` desaparece e `prompt_progress.cache` excede 85–90% em turnos seguidos.
3. **Atualizar Hermes para `v0.23.4` e cortar injeção de ruído (30 min)**: Executar `hermes update`, desativar *skills* não utilizadas (`hermes skills config` / `opt-out`), confirmar `MALLOC_ARENA_MAX=4`, ativar `llmslim` (ou `hermes-vcc-engine`) com `compression.micro_compact: false` e `compression.threshold_tokens: 16384`, instalar `Toolaria` + `hermes-sentinel` + `hermes-helm`, configurar `web.backend keenable` e `terminal.backend bubblewrap`.
4. **Ativar controlo dinâmico de raciocínio e escalação (30–45 min)**: Instalar o *plugin* `llm_request` para modular `reasoning_budget` por turno, reforçar o bloqueio de ferramentas sensíveis no Telegram enquanto `#121089` permanecer aberta, e integrar `luandro/hermes-forgecode` para delegar tarefas pesadas de codificação.
5. **Avaliação isolada de `ik_llama.cpp` (opcional)**: Compilar `ik_llama.cpp` num diretório paralelo e comparar `llama-bench -p 512,4096 -n 128` e 5 cenários reais de *tool calling* do Hermes face ao `llama.cpp` v1.0+; promover para produção apenas se confirmar ganho líquido em *prefill* sem regressões no *template* Jinja.