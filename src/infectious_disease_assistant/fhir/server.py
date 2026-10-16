import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.fhir.constants import (
    BASE, REFERENCE_DATE_URL, RESOURCE_TYPES, SYNTHETIC_ONLY_URL,
)
from infectious_disease_assistant.fhir.errors import FhirParseError
from infectious_disease_assistant.fhir.models import RESOURCE_MODELS


@dataclass
class FixtureStore:
    reference_date: date
    resources: dict[str, dict]


def load_fixtures(path: Path | None = None) -> FixtureStore:
    path = path if path is not None else Settings().data_dir / "fhir" / "bundle.json"
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle["resourceType"] != "Bundle" or bundle["type"] != "collection":
            raise ValueError("Expected collection Bundle")
        if {"system": f"{BASE}/CodeSystem/dataset", "code": "synthetic"} not in bundle["meta"]["tag"]:
            raise ValueError("Synthetic dataset marker required")
        reference_date = datetime.fromisoformat(bundle["timestamp"].replace("Z", "+00:00")).date()
        resources = {}
        for entry in bundle["entry"]:
            raw = entry["resource"]
            resource = RESOURCE_MODELS[raw["resourceType"]].model_validate(raw)
            key = f"{raw['resourceType']}/{resource.id}"
            if key in resources:
                raise ValueError("Duplicate resource ID")
            resources[key] = raw
        return FixtureStore(reference_date, resources)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, ValidationError) as error:
        raise FhirParseError("Could not load synthetic FHIR fixtures") from error


def capability_statement(store: FixtureStore) -> dict:
    return {
        "resourceType": "CapabilityStatement", "status": "active", "kind": "instance",
        "date": store.reference_date.isoformat(), "fhirVersion": "4.0.1", "format": ["json"],
        "extension": [
            {"url": SYNTHETIC_ONLY_URL, "valueBoolean": True},
            {"url": REFERENCE_DATE_URL, "valueDate": store.reference_date.isoformat()},
        ],
        "rest": [{"mode": "server", "resource": [
            {"type": kind, "interaction": [{"code": "read"}, {"code": "search-type"}]}
            for kind in RESOURCE_TYPES
        ]}],
    }


def operation_outcome(code: str, message: str) -> dict:
    return {"resourceType": "OperationOutcome", "issue": [
        {"severity": "error", "code": code, "diagnostics": message},
    ]}


def _matches_token(concepts: list[dict], token: str) -> bool:
    for concept in concepts:
        for coding in concept.get("coding", []):
            qualified = f"{coding.get('system', '')}|{coding['code']}"
            if token in {coding["code"], qualified}:
                return True
    return False


def handle_request(store: FixtureStore, method: str, path: str, query: dict[str, list[str]]) -> tuple[int, dict]:
    if method != "GET":
        return 405, operation_outcome("not-supported", "Synthetic endpoint is read-only")
    if path == "/fhir/metadata" and not query:
        return 200, capability_statement(store)
    parts = path.strip("/").split("/")
    if len(parts) not in {2, 3} or parts[0] != "fhir" or parts[1] not in RESOURCE_TYPES:
        return 404, operation_outcome("not-found", "Unknown FHIR endpoint")
    kind = parts[1]
    if len(parts) == 3:
        if query or re.fullmatch(r"[A-Za-z0-9.-]{1,64}", parts[2]) is None:
            return 400, operation_outcome("invalid", "Invalid FHIR read request")
        resource = store.resources.get(f"{kind}/{parts[2]}")
        return (200, resource) if resource else (404, operation_outcome("not-found", "Resource not found"))
    allowed = {"identifier"} if kind == "Patient" else {"patient", "status", "category", "date"}
    if set(query) - allowed or any(len(values) != 1 or not values[0] for values in query.values()):
        return 400, operation_outcome("not-supported", "Unsupported search parameters")
    resources = [item for key, item in store.resources.items() if key.startswith(f"{kind}/")]
    for name, values in query.items():
        value = values[0]
        if name == "identifier":
            resources = [item for item in resources if any(
                value == f"{identifier['system']}|{identifier['value']}"
                for identifier in item.get("identifier", [])
            )]
        elif name == "patient":
            resources = [item for item in resources if
                         (item.get("patient") or item.get("subject", {})).get("reference") == value]
        elif name == "status":
            resources = [item for item in resources if item.get("status") == value]
        elif name == "category":
            resources = [item for item in resources if _matches_token(item.get("category", []), value)]
        elif name == "date":
            try:
                cutoff = date.fromisoformat(value[2:])
                if value[:2] not in {"ge", "le"}:
                    raise ValueError("Unsupported date prefix")
            except ValueError:
                return 400, operation_outcome("invalid", "Use date=geYYYY-MM-DD or date=leYYYY-MM-DD")
            filtered = []
            for item in resources:
                day = item.get("effectiveDateTime") or item.get("recordedDate") or item.get("period", {}).get("start")
                if day is None:
                    return 400, operation_outcome("not-supported", "Resource has no supported date")
                observed = date.fromisoformat(day[:10])
                if (value[:2] == "ge" and observed >= cutoff) or (value[:2] == "le" and observed <= cutoff):
                    filtered.append(item)
            resources = filtered
    return 200, {"resourceType": "Bundle", "type": "searchset", "total": len(resources),
                 "entry": [{"resource": item} for item in resources]}


def make_server(store: FixtureStore, port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            parsed = urlsplit(self.path)
            status, payload = handle_request(store, self.command, parsed.path, parse_qs(parsed.query, keep_blank_values=True))
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/fhir+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = respond
        do_POST = respond
        do_PUT = respond
        do_PATCH = respond
        do_DELETE = respond

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve read-only synthetic FHIR R4 fixtures on loopback.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        with make_server(load_fixtures(), args.port) as server:
            print(f"Synthetic FHIR R4 endpoint: http://127.0.0.1:{server.server_port}/fhir", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (FhirParseError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
