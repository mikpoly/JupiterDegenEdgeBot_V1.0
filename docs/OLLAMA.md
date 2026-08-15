# Ollama setup

The bot uses **`qwen2.5:1.5b-instruct-q4_K_M`** as its default local AI reviewer.

## Windows

```powershell
irm https://ollama.com/install.ps1 | iex
ollama pull qwen2.5:1.5b-instruct-q4_K_M
ollama list
```

Or from the project folder:

```powershell
.\INSTALL_OLLAMA.ps1 -InstallIfMissing
.\OLLAMA_STATUS.ps1
```

The default API URL expected by the bot is `http://127.0.0.1:11434`. If Ollama is installed but not responding, start the Ollama application or run `ollama serve` in a separate terminal.

You may change `OLLAMA_MODEL` in `.env`, but other models have not been validated with this V1.0 configuration.
