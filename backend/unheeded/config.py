"""Settings from the environment, falling back to the repo-root .env (no extra dependency)."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.split(" #")[0].strip())


USERS = {"agent_ro": "AGENT_RO_PASSWORD", "ws_writer": "WS_WRITER_PASSWORD"}  # see docker-compose.yml


def connect(user=None):
    """user=None: local admin (loader only). 'agent_ro': FalkorDB refuses every write. 'ws_writer': ws_* graphs only."""
    from falkordb import FalkorDB
    kw = {}
    if user:
        password = os.environ.get(USERS[user])
        if not password:
            raise RuntimeError(f"{USERS[user]} not set (see .env.example)")  # fail closed: never fall back to admin
        kw = {"username": user, "password": password}
    return FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"), port=int(os.environ.get("FALKORDB_PORT", 6379)), **kw)


def llm():
    """Chat model from LLM_PROVIDER: 'stub' (offline, deterministic; tests and evals) or 'openrouter'.
    No sampling params are sent: current Claude models reject temperature/top_p."""
    provider = os.environ.get("LLM_PROVIDER", "stub")
    if provider == "stub":
        from unheeded.stub_llm import StubChat
        return StubChat()
    if provider == "openrouter":
        key, model = os.environ.get("OPENROUTER_API_KEY"), os.environ.get("LLM_MODEL")
        if not key or not model:
            raise RuntimeError("LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY and LLM_MODEL")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, api_key=key, base_url="https://openrouter.ai/api/v1", max_tokens=8000, timeout=180,
                          max_retries=2, default_headers={"X-Title": "Unheeded"})
    # ponytail: add 'anthropic' (langchain-anthropic) when a direct Anthropic key is in play
    raise RuntimeError(f"unknown LLM_PROVIDER {provider!r}")
