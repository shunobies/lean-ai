# Using llama-server (llama.cpp)

Lean AI works with [llama.cpp](https://github.com/ggerganov/llama.cpp)'s built-in HTTP server (`llama-server`) as an alternative to Ollama. This gives you direct control over inference parameters, KV cache management, and advanced features like prompt caching and grammar-constrained generation.

No code changes are needed — llama-server exposes an OpenAI-compatible API, so you point Lean AI's OpenAI provider at it.

## Quick Setup

### 1. Build or install llama.cpp

Building from source is recommended — especially on Linux with AMD GPUs, where pre-built binaries may not match your ROCm version.

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
```

**NVIDIA (CUDA):**
```bash
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j$(nproc)
```

**AMD (ROCm/HIP):**
```bash
cmake -B build -DGGML_HIP=ON
cmake --build build --config Release -j$(nproc)
```

ROCm 5.7+ is required. CMake detects your ROCm installation automatically. If it doesn't find HIP, set `CMAKE_PREFIX_PATH=/opt/rocm`.

**CPU only:**
```bash
cmake -B build
cmake --build build --config Release -j$(nproc)
```

The server binary is at `build/bin/llama-server`.

Pre-built binaries (primarily CUDA) are available from the [releases page](https://github.com/ggerganov/llama.cpp/releases).

### 2. Download a GGUF model

```bash
# Example: Qwen3 Coder 30B at Q4_K_M quantization
huggingface-cli download Qwen/Qwen3-Coder-30B-A3B-GGUF \
  qwen3-coder-30b-a3b-q4_k_m.gguf \
  --local-dir models/
```

### 3. Start the server

```bash
llama-server \
  -m models/qwen3-coder-30b-a3b-q4_k_m.gguf \
  --port 8080 \
  -c 131072 \
  -ngl 99 \
  --cache-type-k q8_0 \
  --cache-type-v q4_0
```

| Flag | Purpose |
|---|---|
| `-m` | Path to the GGUF model file |
| `--port` | HTTP port (default 8080) |
| `-c` | Context window size in tokens |
| `-ngl` | Layers to offload to GPU (`99` = all) |
| `--cache-type-k` | KV cache key quantization (reduces VRAM) |
| `--cache-type-v` | KV cache value quantization |

### 4. Configure Lean AI

```env
LEAN_AI_LLM_PROVIDER=openai
LEAN_AI_OPENAI_API_KEY=not-needed
LEAN_AI_OPENAI_BASE_URL=http://localhost:8080/v1
LEAN_AI_OPENAI_MODEL=qwen3-coder
LEAN_AI_OPENAI_CONTEXT_WINDOW=128
LEAN_AI_OPENAI_TEMPERATURE=0.7
```

The `API_KEY` is required by the OpenAI SDK but llama-server ignores it — any non-empty string works.

**Important:** Keep `LEAN_AI_OPENAI_CONTEXT_WINDOW` in sync with the `-c` flag. [Shorthand](configuration.md#context-window-shorthand) works here — `128` means 131072.

> **Ollama is still needed** for inline predictions and embeddings, even when using llama-server as the primary provider. Make sure Ollama is running alongside llama-server.

## Why llama-server?

Ollama is the simplest way to run local models, but llama-server gives you several advantages for power users:

### KV Cache Quantization

Ollama stores the KV cache in FP16 by default. llama-server lets you quantize it, cutting VRAM usage significantly:

```bash
# Asymmetric quantization — keys need more precision than values
llama-server -m model.gguf -c 131072 \
  --cache-type-k q8_0 \
  --cache-type-v q4_0
```

At 128k context, this roughly halves the KV cache VRAM compared to FP16. Quality impact is minimal for most coding tasks.

### KV Cache RAM Offloading

If your GPU can't fit both the model weights and a large KV cache, you can move the KV cache to system RAM:

```bash
llama-server -m model.gguf -c 131072 \
  -ngl 99 \
  --no-kv-offload
```

The `--no-kv-offload` flag keeps model layers on the GPU but stores the KV cache in system RAM. This trades latency for the ability to run context windows that wouldn't otherwise fit.

**Performance impact:**

- **Prompt processing** (reading files, plans, context): minimal slowdown — this is compute-bound on the GPU regardless of where the KV cache lives.
- **Token generation**: moderate slowdown. Each generated token requires reading the full KV cache, so generation speed depends on your RAM bandwidth. Expect roughly 30–50% slower token generation with DDR5, more with DDR4.
- **Net effect for coding**: the slowdown primarily affects token generation (writing code), not prompt processing (reading context). Since agentic coding spends most of its time reading and planning, the practical impact is smaller than the raw numbers suggest.

This is particularly useful for running larger models at full context — if the KV cache alone would consume 8–12 GB of VRAM, offloading it to RAM frees that space for the model weights.

### Prompt Caching with Slots

llama-server supports prompt prefix caching through its slot system. When a new request shares a prefix with a previous one (common in multi-turn conversations), the server reuses the cached KV state instead of reprocessing:

```bash
llama-server -m model.gguf -c 131072 \
  --slot-save-path cache/ \
  -np 1
```

| Flag | Purpose |
|---|---|
| `--slot-save-path` | Directory to persist slot cache to disk |
| `-np` | Number of parallel slots (1 for single-user) |

This can dramatically speed up follow-up turns where the system prompt and project context haven't changed.

### Grammar-Constrained Generation (GBNF)

llama-server supports GBNF grammars that force the model's output to conform to a specific structure. While Lean AI doesn't currently expose this through its API, you can configure it at the server level for experiments with structured output.

### Speculative Decoding

Use a smaller draft model to speed up generation from a larger model:

```bash
llama-server \
  -m models/qwen3-coder-30b.gguf \
  -md models/qwen3-coder-1.5b.gguf \
  -c 131072 \
  -ngl 99 \
  --draft-max 16
```

The draft model proposes tokens in batches, and the main model verifies them. When the draft model's predictions are accepted (common for boilerplate code), generation speed improves substantially.

### Multi-GPU Tensor Splitting

If you have multiple GPUs, llama-server can split model layers across them with `--tensor-split`:

```bash
# Even split across 2 GPUs
llama-server -m model.gguf -c 131072 -ngl 99 \
  --tensor-split 0.5,0.5

# Uneven split (e.g., 16 GB + 8 GB GPUs — give more to the bigger card)
llama-server -m model.gguf -c 131072 -ngl 99 \
  --tensor-split 0.65,0.35
```

The ratios control what fraction of layers each GPU gets. This lets you run models that don't fit on a single card, or spread the workload to increase throughput. Adjust the split based on each GPU's available VRAM.

## Example Configurations

### Maximum Context on Limited VRAM (24 GB)

Run a 30B model with 128k context by combining KV cache quantization and RAM offloading:

```bash
llama-server \
  -m models/qwen3-coder-30b-a3b-q4_k_m.gguf \
  -c 131072 \
  -ngl 99 \
  --cache-type-k q8_0 \
  --cache-type-v q4_0 \
  --no-kv-offload
```

```env
LEAN_AI_LLM_PROVIDER=openai
LEAN_AI_OPENAI_API_KEY=not-needed
LEAN_AI_OPENAI_BASE_URL=http://localhost:8080/v1
LEAN_AI_OPENAI_MODEL=qwen3-coder
LEAN_AI_OPENAI_CONTEXT_WINDOW=128
```

### Speed-Optimized with Speculative Decoding

Maximize generation speed with a draft model and GPU-resident KV cache:

```bash
llama-server \
  -m models/qwen3-coder-30b-a3b-q4_k_m.gguf \
  -md models/qwen3-coder-1.5b-q8_0.gguf \
  -c 65536 \
  -ngl 99 \
  --cache-type-k q8_0 \
  --cache-type-v q4_0 \
  --draft-max 16
```

```env
LEAN_AI_OPENAI_CONTEXT_WINDOW=64
```

### Multi-GPU (AMD ROCm)

Spread a large model across two AMD GPUs with KV cache quantization:

```bash
llama-server \
  -m models/qwen3-coder-30b-a3b-q4_k_m.gguf \
  -c 131072 \
  -ngl 99 \
  --tensor-split 0.5,0.5 \
  --cache-type-k q8_0 \
  --cache-type-v q4_0
```

### Remote Server

Run llama-server on a different machine (e.g., a GPU workstation):

```bash
# On the GPU machine
llama-server -m model.gguf -c 131072 -ngl 99 --host 0.0.0.0 --port 8080
```

```env
# On the Lean AI machine
LEAN_AI_OPENAI_BASE_URL=http://gpu-workstation:8080/v1
```

## Limitations

- **No automatic model management** — unlike Ollama, you download and manage GGUF files manually.
- **Single model per instance** — each llama-server process serves one model. Run multiple instances on different ports if you need separate models for primary inference and inline predictions.
- **Tool calling support varies** — llama-server's tool/function calling support depends on the model and server version. If tool calls aren't working, ensure you're using a recent build and a model that supports tool use.
- **No embeddings endpoint** — Ollama is still required for embeddings and inline predictions.

## Further Reading

- [llama.cpp server documentation](https://github.com/ggerganov/llama.cpp/blob/master/tools/server/README.md)
- [GGUF model format](https://github.com/ggerganov/ggml/blob/master/docs/gguf.md)
- [Configuration Reference](configuration.md) — All Lean AI environment variables
- [Modelfile Guide](modelfile.md) — Customizing Ollama models (for the required Ollama instance)
