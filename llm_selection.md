# Local LLM Selection

## Selection

- Backend: Ollama `0.24.0`, installed under `~/.local/opt/ollama`.
- Endpoint: `http://127.0.0.1:11434` only.
- Selected model: `gemma4:e4b-it-q4_K_M`.
- Selected artifact size: 9.6 GB, quantized.

## Hardware Basis

The system inventory found an NVIDIA GeForce RTX 4080 with 16,376 MiB total VRAM. At
Ollama startup, desktop graphics usage left 14.6 GiB available for inference.

Gemma 4 is available in Ollama. The requested 16-24 GB decision branch suggests trying
the 26B A4B/MoE quantized model. Its official Q4 artifact is 18 GB, larger than this
host's measured available VRAM before context/cache overhead, so it would require
offload and is not the reliable interactive choice for browser work. The quantized E4B
model fits with headroom for browser and model context.

## Safety Configuration

Ollama is launched with:

```bash
OLLAMA_HOST=127.0.0.1:11434 \
OLLAMA_MODELS="$HOME/.local/share/ollama/models" \
OLLAMA_NO_CLOUD=true \
"$HOME/.local/bin/ollama" serve
```

No inference API is bound to a LAN or Tailscale address.

## Verification

The endpoint listener and version response were verified locally. Model pull and API
prompt verification are recorded during installation and must complete before agent
workflows relying on LLM generation are used.

## Sources

- Ollama Gemma 4 tags: https://ollama.com/library/gemma4/tags
- Ollama Linux installation documentation: https://docs.ollama.com/linux
- Browser Use supported Ollama integration: https://docs.browser-use.com/open-source/supported-models
