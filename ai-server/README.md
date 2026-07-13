# Webtree AI server (Windows / RTX 5060 Ti 16GB)

Serves **Qwen3-30B-A3B-Instruct-2507 (Q4_K_M GGUF)** via llama.cpp's
`llama-server` — an OpenAI-compatible API the site-generator backend consumes
over Tailscale. The Mac keeps its RAM; the RTX box does the heavy reasoning.

> **Two ways to run this box** — pick one in the site-generator `.env`:
> 1. **Single remote Ollama, swap-on-demand** (simplest): both the content and
>    reasoning models behind one Ollama with `OLLAMA_MAX_LOADED_MODELS=1`.
>    See the "Single-GPU AI-server setup" block in `.env.example`.
> 2. **This deployment** (fastest content generation): llama-server keeps
>    Qwen3-30B resident with expert-only CPU offload (`--n-cpu-moe`) — no model
>    swaps, attention + KV always on GPU. GLM for the reasoning role then runs
>    alongside (see "second model" below) or stays on the Mac.

Copy this directory to the Windows machine (anywhere, e.g. `C:\webtree-ai`) and
run everything below in PowerShell there.

## 1. Prerequisites

- **Docker Desktop** with the WSL2 backend (Settings → General → "Use the
  WSL 2 based engine").
- **Current NVIDIA driver** on Windows. The RTX 5060 Ti is Blackwell (sm_120)
  and needs a recent driver; the WSL2 CUDA stack comes with it — do NOT install
  a driver inside WSL.
- **Tailscale** installed and logged in on both the Windows box and the Mac.

Verify GPU passthrough works before anything else:

```powershell
docker run --rm --gpus=all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

You should see the RTX 5060 Ti listed. If not, update Docker Desktop and the
NVIDIA driver first.

> **Blackwell note:** if `llama-server` later fails with a CUDA arch error
> (sm_120 unsupported), pull the newest image — `docker compose pull` — the
> `server-cuda` tag is rebuilt frequently and current builds target Blackwell.

## 2. Start the server

```powershell
cd C:\webtree-ai
docker compose up -d
docker compose logs -f llm   # first start downloads ~18.6GB of weights
```

Ready when the log shows the HTTP server listening. Smoke test locally:

```powershell
curl.exe http://localhost:8080/health
curl.exe http://localhost:8080/v1/models
```

## 3. Firewall (one-time, admin PowerShell)

Tailscale traffic still passes Windows Defender Firewall. Allow the port for
tailnet peers only (100.64.0.0/10 is the Tailscale CGNAT range):

```powershell
New-NetFirewallRule -DisplayName "llama-server (tailnet only)" -Direction Inbound `
  -Protocol TCP -LocalPort 8080 -RemoteAddress 100.64.0.0/10 -Action Allow
```

Find this box's tailnet IP with `tailscale ip -4` (a 100.x.y.z address).

## 4. Tune `--n-cpu-moe` (the performance knob)

The Q4_K_M weights (~18.6GB) don't fully fit in 16GB VRAM. `--n-cpu-moe N`
keeps the MoE **expert** tensors of the first N layers in CPU RAM while
attention layers and the KV cache stay on the GPU. Lower N = more on GPU =
faster, until you OOM.

1. Start with the shipped `--n-cpu-moe 14`.
2. Run a generation (or the smoke-test completion below) and watch
   `nvidia-smi` in another terminal.
3. VRAM comfortably under ~15.5GB and stable → lower N by 2 in
   `docker-compose.yml`, `docker compose up -d`, repeat.
4. CUDA out-of-memory in the logs → raise N by 2.
5. Keep the lowest N that never OOMs (leave ~0.5GB headroom — the desktop
   compositor also uses this GPU).

## 5. Point the site-generator at this box (on the Mac)

In the site-generator repo's root `.env`:

```
LLM_BACKEND=mlx
MLX_BASE_URL=http://<windows-tailnet-ip>:8080
MLX_MODEL=qwen3-30b-a3b
MLX_TIMEOUT_SECONDS=120
MLX_MAX_TOKENS=8192
SCAFFOLD_NUM_CTX=16384
```

Notes:
- `MLX_BASE_URL`: use the 100.x.y.z IP. The MagicDNS name may resolve inside
  the backend container, but the raw IP always works.
- `MLX_MODEL` must match `--alias` in this compose file.
- `MLX_TIMEOUT_SECONDS=120`: this is a per-read (between-token) timeout; the
  remote GPU streams fast and the model stays resident, so the Mac-tuned 600s
  default is unnecessary slack.
- `SCAFFOLD_NUM_CTX=16384` pairs with the server's `-c 16384` — the planner
  packs more pages per call (fewer batch splits ⇒ better cross-page coherence).
  If you shrink `-c`, shrink this to match.

Then on the Mac: `./dev.sh up` — it detects the remote `MLX_BASE_URL`, skips
the local MLX server, and health-checks this box instead.

End-to-end check from the Mac:

```bash
curl -sf http://<windows-tailnet-ip>:8080/v1/models
curl -s http://<windows-tailnet-ip>:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-30b-a3b","response_format":{"type":"json_object"},
       "messages":[{"role":"system","content":"Reply with JSON."},
                   {"role":"user","content":"{\"ping\": true} — echo this back as JSON."}]}'
```

## Optional: a second model for the reasoning role (GLM)

The backend has a separate "reasoning" role (`REASONING_*` in the site-generator
`.env` — see `backend/app/config.py`) that routes the small judgment-heavy calls
(brand detection, design brain, image judge) to a different model, typically GLM.
Two ways to host it on this same box:

- **Ollama alongside llama-server** (simplest — auto load/unload):
  install Ollama for Windows, `ollama pull glm-z1:9b-q8_0`, then on the Mac set
  `REASONING_BACKEND=ollama`, `REASONING_BASE_URL=http://<windows-tailnet-ip>:11434`,
  `REASONING_MODEL=glm-z1:9b-q8_0`. Add a firewall rule for port 11434 (same
  command as §3 with `-LocalPort 11434`).
- **A second llama-server service** in this compose file on another port
  (e.g. 8081) with a GLM GGUF.

Either way the two models share the 16GB card: a 9B Q4 needs ~6GB, so raise
`--n-cpu-moe` on the Qwen service (e.g. to ~28-32) to free that VRAM, or accept
that Ollama will run GLM partly on CPU. Re-run the §4 tuning loop after any
change. The reasoning calls are small and infrequent — CPU-heavy GLM is usually
fine; keep the VRAM priority on the content model.

## Why these llama-server flags

| Flag | Reason |
| --- | --- |
| `-hf unsloth/...GGUF:Q4_K_M` | Auto-downloads the model into the `llama_cache` volume on first start. |
| `--alias qwen3-30b-a3b` | Stable model id exposed on `/v1/models`; matches `MLX_MODEL` on the Mac. |
| `-ngl 999 --n-cpu-moe 14` | All layers on GPU except the expert tensors of the first 14 (see §4). |
| `-c 16384` | Context window; matches `SCAFFOLD_NUM_CTX` on the Mac. |
| `-fa on` | Flash attention — faster prefill, less VRAM. |
| `--cache-type-k/v q8_0` | Quantized KV cache: ~0.8GB at 16k ctx instead of ~1.6GB f16. |
| `--jinja` | Applies the model's chat template server-side, honoring the backend's `chat_template_kwargs.enable_thinking` and `response_format: {"type":"json_object"}`. Required. |
