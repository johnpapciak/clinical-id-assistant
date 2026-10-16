import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from infectious_disease_assistant.config import RagSettings
from infectious_disease_assistant.rag.database import GuidelineStore
from infectious_disease_assistant.rag.errors import RagError
from infectious_disease_assistant.rag.models import GuidelineSearch
from infectious_disease_assistant.rag.service import ingest_guidelines, search_guidelines


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest and search demo guidelines in PostgreSQL/pgvector.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Apply bundled database migrations; no embedding API call")
    ingest = commands.add_parser("ingest", help="Extract, clean, chunk, embed, and persist Markdown documents")
    ingest.add_argument("--manifest", type=Path)
    search = commands.add_parser("search", help="Embed a query and retrieve nearest guideline chunks")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    try:
        if args.command == "init":
            GuidelineStore(RagSettings()).initialize()
            print("Guideline database initialized.")
        elif args.command == "ingest":
            print(ingest_guidelines(args.manifest).model_dump_json(indent=2))
        else:
            GuidelineSearch(query=args.query, top_k=args.top_k)
            print(json.dumps([match.model_dump(mode="json") for match in search_guidelines(args.query, args.top_k)], indent=2))
    except ValidationError:
        print("Error: check RAG inputs and database/embedding configuration.", file=sys.stderr)
        return 1
    except RagError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
