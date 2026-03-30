"""
notebooklm_sync.py — Push/pull trading data to/from Google NotebookLM.

Usage:
    python notebooklm_sync.py push-docs        # Upload strategy docs to "ACB Strategy" notebook
    python notebooklm_sync.py push-backtest    # Upload latest backtest CSV to "ACB Backtest Results"
    python notebooklm_sync.py push-trades      # Upload session_state.json trade log
    python notebooklm_sync.py list             # List all notebooks
    python notebooklm_sync.py ask <notebook> <question>  # Chat with a notebook
    python notebooklm_sync.py sync             # Run push-docs + push-backtest + push-trades
"""

import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Notebook names
# ---------------------------------------------------------------------------
NOTEBOOK_STRATEGY = "ACB Strategy"
NOTEBOOK_BACKTEST = "ACB Backtest Results"
NOTEBOOK_TRADES   = "ACB Trade Log"

# Files to push into each notebook
STRATEGY_SOURCES = [
    Path("README.md"),
    Path("PLAN.md"),
    Path("CLAUDE.md"),
    Path("_agents/agent.md"),
    Path("_agents/skills/stacy_burke_trading/SKILL.md"),
]

BACKTEST_SOURCES = [
    Path("backtest_results.csv"),
    Path("backtest_discards_summary.csv"),
    Path("backtest_discards_would_have_hit.csv"),
]

TRADE_SOURCES = [
    Path("session_state.json"),
    Path("paused_setups.json"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_or_create_notebook(client, name: str):
    """Return existing notebook by name or create a new one."""
    notebooks = await client.notebooks.list()
    for nb in notebooks:
        if nb.title == name:
            return nb
    print(f"  Creating notebook: {name}")
    return await client.notebooks.create(name)


async def push_files(client, notebook_name: str, paths: list[Path]):
    """Upload local files as sources to a notebook."""
    nb = await get_or_create_notebook(client, notebook_name)
    existing = await client.sources.list(nb.id)
    existing_titles = {s.title for s in existing}

    for path in paths:
        if not path.exists():
            print(f"  SKIP (not found): {path}")
            continue
        if path.name in existing_titles:
            print(f"  SKIP (already uploaded): {path.name}")
        print(f"  Uploading: {path}")
        try:
            await client.sources.add_file(nb.id, str(path), wait=True)
        except Exception as e:
            print(f"  ERROR uploading {path}: {e}")
            continue

    print(f"  Done → {notebook_name}")
    return nb


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_list(client):
    notebooks = await client.notebooks.list()
    if not notebooks:
        print("No notebooks found.")
        return
    for nb in notebooks:
        print(f"  [{nb.id}] {nb.title}")


async def cmd_push_docs(client):
    print(f"Pushing strategy docs → '{NOTEBOOK_STRATEGY}'")
    await push_files(client, NOTEBOOK_STRATEGY, STRATEGY_SOURCES)


async def cmd_push_backtest(client):
    print(f"Pushing backtest data → '{NOTEBOOK_BACKTEST}'")
    await push_files(client, NOTEBOOK_BACKTEST, BACKTEST_SOURCES)


async def cmd_push_trades(client):
    print(f"Pushing trade log → '{NOTEBOOK_TRADES}'")
    await push_files(client, NOTEBOOK_TRADES, TRADE_SOURCES)


async def cmd_ask(client, notebook_name: str, question: str):
    notebooks = await client.notebooks.list()
    nb = next((n for n in notebooks if n.title == notebook_name), None)
    if nb is None:
        print(f"Notebook not found: {notebook_name}")
        print("Available:", [n.title for n in notebooks])
        return
    print(f"Asking '{notebook_name}': {question}")
    result = await client.chat.ask(nb.id, question)
    print("\n" + result.answer)


async def cmd_sync(client):
    await cmd_push_docs(client)
    await cmd_push_backtest(client)
    await cmd_push_trades(client)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    try:
        from notebooklm import NotebookLMClient
    except ImportError:
        print("notebooklm-py not installed. Run: pip install 'notebooklm-py[browser]'")
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    async with await NotebookLMClient.from_storage() as client:
        if cmd == "list":
            await cmd_list(client)
        elif cmd == "push-docs":
            await cmd_push_docs(client)
        elif cmd == "push-backtest":
            await cmd_push_backtest(client)
        elif cmd == "push-trades":
            await cmd_push_trades(client)
        elif cmd == "sync":
            await cmd_sync(client)
        elif cmd == "ask":
            if len(args) < 3:
                print("Usage: python notebooklm_sync.py ask <notebook-name> <question>")
                sys.exit(1)
            await cmd_ask(client, args[1], " ".join(args[2:]))
        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
