"""Embeds the tenant's lookup pages and writes knowledge/lookup-index.json (app/knowledge_search.py).

    OPENAI_API_KEY=... uv run python scripts/build_knowledge_index.py [tenant folder]

Run it after editing a page listed under `lookup_knowledge` in agent.yaml, then commit the index.
Embedding about a hundred short passages costs a fraction of a cent. A test fails while the
committed index no longer matches the pages.
"""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import knowledge_search


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tenants" / "summit-air"
    agent = yaml.safe_load((folder / "agent.yaml").read_text())
    documents = {
        name: (folder / "knowledge" / f"{name}.md").read_text() for name in agent["lookup_knowledge"]
    }
    index = knowledge_search.build_index(documents)
    out = folder / "knowledge" / knowledge_search.INDEX_FILE
    out.write_text(json.dumps(index, separators=(",", ":")) + "\n")
    print(f"{len(index['passages'])} passages from {len(documents)} pages -> {out}")


if __name__ == "__main__":
    main()
