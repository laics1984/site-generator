# Webtree AI server

The **only** place that decides how and where a model runs. The site-generator
backend holds one setting — `LLM_BASE_URL` — and reads the model id from
`/v1/models` at runtime. So swapping Qwen for DeepSeek, GLM or Kimi is one line
in `ai-server/.env`, with no backend edit and no backend restart.

That works because every engine here serves the same **OpenAI-compatible** API
(`/v1/chat/completions` + `/v1/models`), whichever one you pick:

| `LLM_ENGINE` | What it is | Where it runs |
| --- | --- | --- |
| `ollama` (default) | Easiest model swapping — registry tags, auto load/unload | container, GPU |
| `llamacpp` | Manual control of the GPU/CPU split (`--n-cpu-moe`) and sampling flags | container, GPU |
| `mlx` | Apple Silicon only — Metal can't be containerized, so it's a host process | host-native |
| `external` | Already running elsewhere: a remote box, Windows-native Ollama, LM Studio | nothing started |

Always drive it through `./ai.sh`, never `docker compose` directly — the script
picks the profile, layers the Tailscale overlay, and waits for readiness.

```bash
./ai.sh up      # start and block until /v1/models answers
./ai.sh status  # engine, model, readiness, containers
./ai.sh logs    # follow engine logs
./ai.sh pull    # fetch/refresh the model named in .env
./ai.sh down
```

---

## A. Run it on this box (WSL2 + Docker Desktop)

Prerequisites, all verified once:

1. **Docker Desktop running**, with Settings → Resources → WSL Integration
   enabled for your distro. The Docker daemon lives in Docker Desktop, so
   `wsl --shutdown` takes it and every container down with it.
2. **Current NVIDIA driver on Windows.** Do *not* install a driver inside WSL.
   Verify GPU passthrough before anything else:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
   ```
   Your card should be listed. `nvidia-smi` isn't in that image — the NVIDIA
   Container Toolkit injects it from the host, which is exactly why this proves
   the whole chain works.
3. **Enough WSL RAM.** A 24 GB model against a 16 GB card leaves ~9-10 GB of
   experts in system RAM, and WSL takes only half the machine's RAM by default.
   Create `C:\Users\<you>\.wslconfig`:
   ```ini
   [wsl2]
   memory=24GB
   processors=16
   swap=8GB
   ```
   Then `wsl --shutdown` **from PowerShell** (running it inside WSL kills the
   calling session) and reopen. `memory` is a ceiling, not a reservation — WSL
   allocates on demand and hands freed pages back to Windows. Check with
   `free -g`.
4. **A free GPU.** Stop any Windows-side Ollama/LM Studio holding VRAM;
   `nvidia-smi` should show the card near idle.

Then:

```bash
cp .env.example .env   # edit LLM_ENGINE / LLM_MODEL if you want something else
./ai.sh up             # first run downloads ~24GB
```

## B. Swap the model

Edit **one line** in `.env` and re-run `./ai.sh up`. The backend picks it up on
its next call — no restart.

```
LLM_MODEL=qwen3.6:35b-a3b
```

| Model | `LLM_ENGINE=ollama` | `LLM_ENGINE=llamacpp` (`LLM_HF_REPO`) |
| --- | --- | --- |
| Qwen3.6 35B-A3B | `qwen3.6:35b-a3b` | `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` |
| …NVFP4 (Blackwell) | `qwen3.6:35b-a3b-nvfp4` | — |
| …MTP (speculative) | `qwen3.6:35b-a3b-mtp-q4_K_M` | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_M` |
| DeepSeek | `deepseek-r1:32b` | `unsloth/DeepSeek-R1-Distill-Qwen-32B-GGUF:Q4_K_M` |
| GLM (good reasoning-role companion) | `glm-z1:9b` | — |
| Kimi | `kimi-k2:latest` | — |

Verify a tag exists at `https://ollama.com/library/<name>/tags` before pulling.

**One caveat, specific to the `ollama` engine.** Ollama advertises *every pulled
model* on `/v1/models`, not just the one `LLM_MODEL` names — and the backend
discovers its model from that list, picking the alphabetically first when there
are several. So with more than one model pulled, editing `LLM_MODEL` here is
**not** by itself authoritative. `./ai.sh up` and `./ai.sh status` warn when this
applies. Either remove the ones you don't want:

```bash
docker compose exec ollama ollama rm <old-tag>
```

or pin `LLM_MODEL` in the site-generator `.env` (which does require a backend
restart — the trade-off for keeping several models resident). `llamacpp` and
`mlx` serve exactly one model each, so the ambiguity cannot arise there.

**Reading the tags.** `a3b` and `q4_K_M` describe *different axes*: `a3b` is the
architecture (MoE, 3B active of 35B total), `q4_K_M` is the quantization. A bare
`35b-a3b` tag already resolves to Q4_K_M, so `35b-a3b` and `35b-a3b-q4_K_M` are
the same download.

**Sizing.** On a 16 GB card anything over ~15 GB spills experts to system RAM.
That is fine for an MoE with few active parameters and slow for a **dense**
model of the same size, because every parameter is touched per token — prefer
MoE (`-a3b`-style) tags.

### A model can override `LLM_CTX` — check after every swap

`LLM_CTX` sets `OLLAMA_CONTEXT_LENGTH`, which is only a *default*. A model whose
own Modelfile pins `num_ctx` wins, silently. Measured on
`nutboy02/...abliterated-uncenfull` with `LLM_CTX=16384`:

```
    quantization        Q6_K
    num_ctx             262144      ← the model's own value, 16x what you asked for
```

The cost is not academic. The oversized KV cache pushed the resident footprint
from 28 GB to **32 GB** and the split from 42%/58% to **55%/45% CPU/GPU**, and
schema calls averaged ~155s against ~43s for the base model. It also breaks the
`LLM_CTX` ↔ `LLM_CONTEXT_TOKENS` pairing: the backend sizes its batches for 16k
while the server is serving 256k.

`./ai.sh up` and `./ai.sh status` now detect and report this. To force your
value, derive a model — it re-uses the same weight blobs, so it is instant and
costs no extra disk:

```bash
docker compose exec -T ollama sh -c \
  'printf "FROM <model>\nPARAMETER num_ctx 16384\n" > /tmp/Mf && ollama create <name> -f /tmp/Mf'
```

Then point `LLM_MODEL` at `<name>`. Verify with `ollama show <name>`.

**After a swap**, re-check two things that are *not* auto-derived:
`LLM_CTX` here, and the batching caps in the site-generator `.env`
(`LLM_CONTEXT_TOKENS`, `MAX_SECTIONS_PER_BATCH`, `MAX_PAGES_PER_BATCH`).
`LLM_CONTEXT_TOKENS` must match `LLM_CTX` — the backend uses it to decide how
much to pack into one call.

## C. Tune (llama.cpp)

`--n-cpu-moe N` keeps the MoE **expert** tensors of the first N layers in CPU
RAM while attention and the KV cache stay on the GPU. Lower N = more on GPU =
faster, until you run out of VRAM. It is an `.env` edit, not a YAML edit:

1. Start at `LLM_N_CPU_MOE=14`.
2. Run a generation and watch `nvidia-smi`.
3. VRAM comfortably under ~15.5 GB and stable → lower N by 2, `./ai.sh up`, repeat.
4. CUDA out-of-memory in `./ai.sh logs` → raise N by 2.
5. Keep the lowest N that never OOMs, leaving ~0.5 GB headroom for the desktop
   compositor.

**Is this worth doing at all?** Ollama ≥0.32 is itself built on llama.cpp and its
fitter already overflows *expert tensor parts* rather than whole layers. Measured
on this box with `qwen3.6:35b-a3b`:

```
load_tensors: offloaded 41/42 layers to GPU
load_tensors:        CUDA0 model buffer size = 12495.57 MiB
load_tensors:    CUDA_Host model buffer size =  9469.79 MiB
common_params_fit_impl: 41 layers (18 overflowing), 13019 MiB used, 2075 MiB free
```

So the automatic split is already close to what `--n-cpu-moe` would produce. The
llama.cpp lever is reclaiming the ~2 GB the fitter leaves unused, plus explicit
control of sampling and KV flags — **not** free speed. Benchmark both on your own
prompts before committing; the other knobs (`OLLAMA_FLASH_ATTENTION`,
`OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_MAX_LOADED_MODELS=1`) are already set for
the ollama profile in `docker-compose.yml`.

### Why these llama-server flags

| Flag | Reason |
| --- | --- |
| `-hf <repo>:<quant>` | Auto-downloads into the `llama_cache` volume on first start |
| `--alias` | Stable id on `/v1/models` — what the backend discovers |
| `-ngl 999 --n-cpu-moe N` | All layers on GPU except the expert tensors of the first N |
| `-c` | Context window; pair with `LLM_CONTEXT_TOKENS` on the backend |
| `-fa on` | Flash attention — faster prefill, less VRAM |
| `--cache-type-k/v q8_0` | Quantized KV cache: roughly half the VRAM of f16 |
| `--jinja` | Applies the model's chat template server-side, honouring `chat_template_kwargs.enable_thinking` and `response_format: {"type":"json_object"}`. Required. |

## D. Remote access over Tailscale

Set in `.env`:

```
TAILSCALE_ENABLED=1
TS_AUTHKEY=tskey-auth-...        # https://login.tailscale.com/admin/settings/keys
TS_HOSTNAME=webtree-ai
```

`./ai.sh up` then layers `docker-compose.tailscale.yml`: a Tailscale sidecar
joins the tailnet and the engine shares its network namespace, so the server is
reachable at `http://webtree-ai:11434` from any tailnet device via MagicDNS —
**no Windows Defender rule and no `100.64.0.0/10` range to manage**. Localhost
access on this box keeps working. On the site-generator side that is just:

```
LLM_BASE_URL=http://webtree-ai:11434
```

The node's identity persists in the `tailscale_state` volume; without it every
restart would burn an auth key and create a duplicate machine.

**Access control, stated plainly:** Ollama has **no API-key support**, so on the
tailnet the ACLs *are* the auth boundary — tag the node
(`TS_EXTRA_ARGS=--advertise-tags=tag:ai`) and write an ACL for that tag.
llama.cpp does support auth: put `--api-key <secret>` in `LLAMACPP_EXTRA_ARGS`
and set the same value as `LLM_API_KEY` in the site-generator `.env`.

## E. Run the model on a Mac (MLX)

MLX is Apple-Silicon-only and has no Linux image, so it runs as a host process
that `ai.sh` starts and stops — the lifecycle still belongs to this directory.

```
LLM_ENGINE=mlx
LLM_MODEL=mlx-community/Qwen3.5-2B-OptiQ-4bit
MLX_VENV=~/mlx-venv
MLX_PORT=8080
```

Install once with `python3 -m venv ~/mlx-venv && ~/mlx-venv/bin/pip install mlx-lm`.
`./ai.sh up` starts it in the background (log: `~/Library/Logs/mlx-server.log`)
and `./ai.sh down` stops it, freeing the memory. On a non-Mac host this engine
fails fast with a clear message rather than hunting for a venv that cannot exist.

## F. Point at something already running (`external`)

```
LLM_ENGINE=external
EXTERNAL_BASE_URL=http://100.x.y.z:8080
```

`./ai.sh up` starts nothing and only health-checks the endpoint, failing fast if
it's unreachable. Use this for a remote AI box, a Windows-native Ollama, or
LM Studio. Set the site-generator's `LLM_BASE_URL` to the same address.
