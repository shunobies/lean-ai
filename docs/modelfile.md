# Modelfile Guide

Ollama supports custom Modelfiles that let you set persistent parameters, system prompts, and templates for your models. This is useful for tuning model behavior across all Lean AI interactions.

## What Is a Modelfile?

A Modelfile is Ollama's equivalent of a Dockerfile — it creates a new model variant with custom settings baked in. Changes persist across restarts without needing environment variables.

## Creating a Custom Model

```dockerfile
# Modelfile.leanai
FROM qwen3-coder:30b

# Sampling parameters (match Lean AI defaults)
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER top_k 20
PARAMETER repeat_penalty 1.05

# Context window
PARAMETER num_ctx 131072
```

Build and use it:

```bash
ollama create qwen3-coder-leanai -f Modelfile.leanai

# Update your .env
# LEAN_AI_OLLAMA_MODEL=qwen3-coder-leanai
```

## Common Customizations

### Larger Context Window

If your GPU supports it, increase the context window:

```dockerfile
FROM qwen3-coder:30b
PARAMETER num_ctx 262144
```

Then set `LEAN_AI_OLLAMA_CONTEXT_WINDOW=256` to match (shorthand for 262144).

### Dedicated Inline Model

Create a fast, small model variant for inline predictions:

```dockerfile
FROM qwen2.5-coder:7b
PARAMETER num_ctx 16384
PARAMETER temperature 0.2
PARAMETER top_p 0.9
```

```bash
ollama create inline-coder -f Modelfile.inline
```

```env
LEAN_AI_INLINE_MODEL=inline-coder
```

### Lower Temperature for Planning

If you want more deterministic planning output:

```dockerfile
FROM qwen3-coder:30b
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 131072
```

### Refiner Model

Create a dedicated model for the [local refiner](reference-library.md#local-refiner):

```dockerfile
FROM qwen3-coder:8b
PARAMETER temperature 0.3
PARAMETER num_ctx 32768
```

```bash
ollama create refiner -f Modelfile.refiner
```

```env
LEAN_AI_REFINER_MODEL=refiner
```

## Parameter Reference

Key Ollama parameters and their effects on Lean AI:

| Parameter | Default | Effect |
|---|---|---|
| `temperature` | `0.7` | Higher = more creative, lower = more deterministic |
| `top_p` | `0.8` | Nucleus sampling threshold |
| `top_k` | `20` | Limits token selection to top K candidates |
| `repeat_penalty` | `1.05` | Penalizes repeated tokens (higher = less repetition) |
| `num_ctx` | varies | Context window size (must match `LEAN_AI_OLLAMA_CONTEXT_WINDOW`) |
| `num_predict` | varies | Max tokens to generate (overridden by Lean AI's `max_tokens`) |

## Tips

- **Keep `num_ctx` in sync** — The Modelfile's `num_ctx` and `LEAN_AI_OLLAMA_CONTEXT_WINDOW` must match. For example, `num_ctx 131072` pairs with `LEAN_AI_OLLAMA_CONTEXT_WINDOW=128`. If they differ, you'll either waste VRAM or hit unexpected truncation.
- **Don't set system prompts** — Lean AI manages its own system prompts. Setting one in the Modelfile will conflict with the agent's instructions.
- **Qwen3 and temperature** — Qwen3 models warn against greedy decoding (`temperature: 0.0`). Use at least `0.3` for Qwen3-based models.
- **Test before committing** — Run `ollama run your-model-name` interactively to verify the model works before pointing Lean AI at it.

## Further Reading

- [Ollama Modelfile documentation](https://github.com/ollama/ollama/blob/main/docs/modelfile.md)
- [Configuration Reference](configuration.md) — All Lean AI environment variables
