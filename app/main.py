
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, STORAGE_DIR
from app.models import DocumentVersion, Node, Selection, Generation
from app.parser import parse_pdf


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATION_DIR = STORAGE_DIR / "generations"

GENERATION_DIR.mkdir(parents=True, exist_ok=True)

DOCUMENTS = {
    1: DATA_DIR / "ct200_manual.pdf",
    2: DATA_DIR / "ct200_manual_v2.pdf",
}

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="CT200 Manual Intelligence API",
    description=(
        "Version-aware PDF processing, section browsing, "
        "document comparison, and test-case generation."
    ),
    version="1.0.0",
)


# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)

    return digest.hexdigest()


def node_to_dict(node: Node) -> dict:
    return {
        "node_id": node.node_id,
        "logical_id": node.logical_id,
        "version": node.version,
        "parent_id": node.parent_id,
        "heading": node.heading,
        "number": node.number,
        "level": node.level,
        "body_text": node.body_text or "",
        "page_start": node.page_start,
        "page_end": node.page_end,
        "source_order": node.source_order,
        "content_hash": node.content_hash,
    }


def get_nodes(db: Session, version: int) -> list:
    return list(
        db.scalars(
            select(Node)
            .where(Node.version == version)
            .order_by(Node.source_order, Node.id)
        ).all()
    )


def get_document_version(db: Session, version: int):
    # Query by the unique version column, not the primary-key ID.
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.version == version
        )
    )


def get_node_or_404(
    db: Session,
    version: int,
    node_id: str,
) -> Node:
    node = db.scalar(
        select(Node).where(
            Node.version == version,
            Node.node_id == node_id,
        )
    )

    if node is None:
        raise HTTPException(
            status_code=404,
            detail="Section not found in that document version.",
        )

    return node


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------

class SelectionRequest(BaseModel):
    version: int = Field(ge=1)
    node_id: str
    label: Optional[str] = None


class GenerationRequest(BaseModel):
    selection_id: int = Field(gt=0)
    count: int = Field(default=4, ge=3, le=5)


# ---------------------------------------------------------
# Health and document listing
# ---------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/documents")
def list_documents():
    with SessionLocal() as db:
        stored_versions = db.scalars(
            select(DocumentVersion).order_by(
                DocumentVersion.version
            )
        ).all()

        stored_by_version = {
            item.version: item for item in stored_versions
        }

        result = []

        for version, path in DOCUMENTS.items():
            record = stored_by_version.get(version)

            result.append({
                "version": version,
                "filename": path.name,
                "file_exists": path.exists(),
                "ingested": record is not None,
                "file_hash": record.file_hash if record else None,
                "ingested_at": record.ingested_at if record else None,
            })

        return result


# ---------------------------------------------------------
# PDF ingestion
# ---------------------------------------------------------

@app.post("/documents/ingest/{version}")
def ingest_document(version: int):
    if version not in DOCUMENTS:
        raise HTTPException(
            status_code=404,
            detail="Unknown document version.",
        )

    pdf_path = DOCUMENTS[version]

    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found: {pdf_path.name}",
        )

    file_hash = sha256_file(pdf_path)

    with SessionLocal() as db:
        existing = get_document_version(db, version)

        # Avoid unnecessary parsing when the source file is unchanged.
        if existing and existing.file_hash == file_hash:
            section_count = len(get_nodes(db, version))

            return {
                "message": "Document already ingested; file unchanged.",
                "version": version,
                "filename": pdf_path.name,
                "section_count": section_count,
                "file_hash": file_hash,
            }

        try:
            parsed_nodes = parse_pdf(pdf_path, version)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"PDF parsing failed: {exc}",
            ) from exc

        # Replace nodes for this version only.
        db.query(Node).filter(
            Node.version == version
        ).delete(synchronize_session=False)

        if existing is None:
            existing = DocumentVersion(
                version=version,
                filename=pdf_path.name,
                file_hash=file_hash,
                ingested_at=now_iso(),
            )
            db.add(existing)
        else:
            existing.filename = pdf_path.name
            existing.file_hash = file_hash
            existing.ingested_at = now_iso()

        for index, item in enumerate(parsed_nodes):
            heading = str(
                item.get("heading") or "Untitled section"
            )
            body_text = str(item.get("body_text") or "")

            # Stable fallback ID when the parser does not provide one.
            node_id = item.get("node_id") or hashlib.sha256(
                f"{version}:{heading}:{index}".encode("utf-8")
            ).hexdigest()[:32]

            content_hash = item.get("content_hash") or hashlib.sha256(
                f"{heading}\n{body_text}".encode("utf-8")
            ).hexdigest()

            db.add(Node(
                version=version,
                node_id=str(node_id),
                logical_id=item.get("logical_id") or None,
                parent_id=item.get("parent_id"),
                heading=heading,
                number=str(item.get("number") or ""),
                level=int(item.get("level") or 0),
                body_text=body_text,
                page_start=item.get("page_start"),
                page_end=item.get("page_end"),
                source_order=int(
                    item.get("source_order", index)
                ),
                content_hash=content_hash,
            ))

        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

        return {
            "message": "Document ingested successfully.",
            "version": version,
            "filename": pdf_path.name,
            "section_count": len(parsed_nodes),
            "file_hash": file_hash,
        }


# ---------------------------------------------------------
# Browse and search sections
# ---------------------------------------------------------

@app.get("/documents/sections")
def list_sections(
    version: int = Query(..., ge=1),
    parent_id: Optional[str] = None,
):
    with SessionLocal() as db:
        if get_document_version(db, version) is None:
            raise HTTPException(
                status_code=409,
                detail="Ingest this document version first.",
            )

        nodes = get_nodes(db, version)

        if parent_id is not None:
            nodes = [
                node for node in nodes
                if node.parent_id == parent_id
            ]

        return [node_to_dict(node) for node in nodes]


@app.get("/nodes/search")
def search_nodes(
    q: str = Query(..., min_length=1),
    version: int = Query(..., ge=1),
):
    with SessionLocal() as db:
        if get_document_version(db, version) is None:
            raise HTTPException(
                status_code=409,
                detail="Ingest this document version first.",
            )

        query = q.casefold()
        matches = []

        for node in get_nodes(db, version):
            searchable_text = (
                (node.heading or "")
                + " "
                + (node.body_text or "")
            )

            if query in searchable_text.casefold():
                matches.append(node_to_dict(node))

        return {
            "version": version,
            "query": q,
            "count": len(matches),
            "results": matches,
        }


@app.get("/nodes/{node_id}")
def get_node(
    node_id: str,
    version: int = Query(..., ge=1),
):
    with SessionLocal() as db:
        if get_document_version(db, version) is None:
            raise HTTPException(
                status_code=409,
                detail="Ingest this document version first.",
            )

        return node_to_dict(
            get_node_or_404(db, version, node_id)
        )


# ---------------------------------------------------------
# Compare document versions
# ---------------------------------------------------------

@app.get("/documents/compare")
def compare_versions(
    from_version: int = Query(1, ge=1),
    to_version: int = Query(2, ge=1),
):
    if from_version == to_version:
        raise HTTPException(
            status_code=400,
            detail="Choose two different document versions.",
        )

    with SessionLocal() as db:
        for version in (from_version, to_version):
            if get_document_version(db, version) is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ingest version {version} first.",
                )

        old_nodes = get_nodes(db, from_version)
        new_nodes = get_nodes(db, to_version)

        # Match sections by logical ID where possible.
        # Fall back to section number and heading.
        old_map = {}
        new_map = {}

        for node in old_nodes:
            key = (
                node.logical_id
                or f"{node.number}:{node.heading}"
            )
            old_map.setdefault(key, node)

        for node in new_nodes:
            key = (
                node.logical_id
                or f"{node.number}:{node.heading}"
            )
            new_map.setdefault(key, node)

        changes = []

        for key in sorted(set(old_map) | set(new_map)):
            old_node = old_map.get(key)
            new_node = new_map.get(key)

            if old_node is None:
                status = "added"
            elif new_node is None:
                status = "removed"
            elif old_node.content_hash != new_node.content_hash:
                status = "modified"
            else:
                status = "unchanged"

            if status != "unchanged":
                changes.append({
                    "logical_id": key,
                    "status": status,
                    "old_heading": (
                        old_node.heading if old_node else None
                    ),
                    "new_heading": (
                        new_node.heading if new_node else None
                    ),
                    "old_text": (
                        old_node.body_text if old_node else None
                    ),
                    "new_text": (
                        new_node.body_text if new_node else None
                    ),
                    "old_page": (
                        old_node.page_start if old_node else None
                    ),
                    "new_page": (
                        new_node.page_start if new_node else None
                    ),
                })

        return {
            "from_version": from_version,
            "to_version": to_version,
            "change_count": len(changes),
            "changes": changes,
        }


# ---------------------------------------------------------
# Version-pinned selections
# ---------------------------------------------------------

@app.post("/selections")
def create_selection(payload: SelectionRequest):
    with SessionLocal() as db:
        if get_document_version(db, payload.version) is None:
            raise HTTPException(
                status_code=409,
                detail="Ingest this document version first.",
            )

        node = get_node_or_404(
            db,
            payload.version,
            payload.node_id,
        )

        selection = Selection(
            version=payload.version,
            node_id=node.node_id,
            logical_id=node.logical_id,
            content_hash=node.content_hash,
            label=payload.label or node.heading,
            created_at=now_iso(),
        )

        db.add(selection)
        db.commit()
        db.refresh(selection)

        return {
            "selection_id": selection.id,
            "version": selection.version,
            "node_id": selection.node_id,
            "logical_id": selection.logical_id,
            "label": selection.label,
            "content_hash": selection.content_hash,
            "created_at": selection.created_at,
        }


@app.get("/selections/{selection_id}/stale")
def check_selection_stale(
    selection_id: int,
    current_version: int = Query(2, ge=1),
):
    with SessionLocal() as db:
        selection = db.get(Selection, selection_id)

        if selection is None:
            raise HTTPException(
                status_code=404,
                detail="Selection not found.",
            )

        if get_document_version(db, current_version) is None:
            raise HTTPException(
                status_code=409,
                detail="Ingest the current document version first.",
            )

        original_node = db.scalar(
            select(Node).where(
                Node.version == selection.version,
                Node.node_id == selection.node_id,
            )
        )

        if original_node is None:
            raise HTTPException(
                status_code=409,
                detail="Original selected section is missing.",
            )

        candidates = []

        if selection.logical_id:
            candidates = db.scalars(
                select(Node).where(
                    Node.version == current_version,
                    Node.logical_id == selection.logical_id,
                )
            ).all()

        # Fallback matching by section number.
        if not candidates and original_node.number:
            candidates = db.scalars(
                select(Node).where(
                    Node.version == current_version,
                    Node.number == original_node.number,
                )
            ).all()

        if not candidates:
            return {
                "selection_id": selection.id,
                "stale": True,
                "content_changed": None,
                "reason": "Section not found in current version.",
                "original_version": selection.version,
                "current_version": current_version,
            }

        current_node = candidates[0]
        content_changed = (
            current_node.content_hash != selection.content_hash
        )

        return {
            "selection_id": selection.id,
            "stale": (
                content_changed
                or current_version != selection.version
            ),
            "content_changed": content_changed,
            "original_version": selection.version,
            "current_version": current_version,
            "original_heading": original_node.heading,
            "current_heading": current_node.heading,
            "original_content_hash": selection.content_hash,
            "current_content_hash": current_node.content_hash,
        }


# ---------------------------------------------------------
# Deterministic test-case generation fallback
# ---------------------------------------------------------

def build_test_cases(node: Node, count: int) -> list:
    text = (node.body_text or "").strip()
    heading = node.heading
    combined = f"{heading}\n{text}"

    numbers = re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:%|mmHg|sec(?:onds?)?|cycles?)?\b",
        combined,
        re.IGNORECASE,
    )

    error_codes = sorted(set(
        re.findall(r"\bE\d+\b", combined, re.IGNORECASE)
    ))

    templates = [
        {
            "test_case_id": "TC-01",
            "title": "Verify documented behavior",
            "preconditions": (
                "Prepare the device according to the selected manual section."
            ),
            "steps": (
                "Perform the operation described in the source section."
            ),
            "expected_result": (
                "Observed behavior conforms to the selected manual version."
            ),
        },
        {
            "test_case_id": "TC-02",
            "title": "Verify boundary conditions",
            "preconditions": (
                "Identify applicable limits from the selected source text."
            ),
            "steps": (
                "Test immediately below, at, and above each applicable "
                "documented boundary."
            ),
            "expected_result": (
                "Boundary behavior matches the documented specification."
            ),
        },
        {
            "test_case_id": "TC-03",
            "title": "Verify error handling",
            "preconditions": (
                f"Relevant documented error identifiers: "
                f"{', '.join(error_codes) or 'None explicitly detected'}."
            ),
            "steps": (
                "Trigger each applicable error condition documented "
                "in the selected section."
            ),
            "expected_result": (
                "Errors are handled according to the source specification."
            ),
        },
        {
            "test_case_id": "TC-04",
            "title": "Verify recovery and repeatability",
            "preconditions": "The device is in a valid initial state.",
            "steps": (
                "Repeat the documented operation and verify recovery "
                "after a failed attempt where applicable."
            ),
            "expected_result": (
                "The operation is repeatable and the device returns "
                "to a valid state."
            ),
        },
        {
            "test_case_id": "TC-05",
            "title": "Verify specification traceability",
            "preconditions": (
                "Record the selected manual version and source section."
            ),
            "steps": (
                "Execute the documented steps and compare observed results "
                "with the source text."
            ),
            "expected_result": (
                "Expected results are traceable to the selected source "
                "without undocumented assumptions."
            ),
        },
    ]

    results = []

    for template in templates[:count]:
        results.append({
            **template,
            "source_section": heading,
            "source_version": node.version,
            "source_node_id": node.node_id,
            "source_excerpt": text[:1200],
            "detected_numeric_values": numbers[:10],
        })

    return results


# ---------------------------------------------------------
# Optional OpenAI test-case generation
# ---------------------------------------------------------

def build_llm_test_cases(node: Node, count: int):
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return build_test_cases(node, count), "deterministic_baseline"

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is missing. Install it using: pip install openai"
        ) from exc

    client = OpenAI(api_key=api_key)

    source_text = (node.body_text or "")[:10000]

    prompt = f"""
Generate exactly {count} software/device test cases using only the
technical-manual source text below.

Rules:
- Do not invent requirements, thresholds, error codes, or behavior.
- If the source lacks information, state that explicitly.
- Make every test case traceable to the source.
- Return valid JSON only, with a top-level "test_cases" array.

Required fields per test case:
title, preconditions, steps, expected_result, source_excerpt

Section heading: {node.heading}
Section number: {node.number}
Document version: {node.version}

Source text:
{source_text}
"""

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate traceable technical test cases. "
                    "Use only supplied source evidence and never invent "
                    "requirements. Return valid JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    response_text = response.choices[0].message.content

    if not response_text:
        raise ValueError("The model returned an empty response.")

    parsed = json.loads(response_text)
    cases = parsed.get("test_cases")

    if not isinstance(cases, list) or len(cases) != count:
        raise ValueError(
            f"Expected exactly {count} test cases from the model."
        )

    required_fields = {
        "title",
        "preconditions",
        "steps",
        "expected_result",
        "source_excerpt",
    }

    normalized = []

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError("Each generated test case must be an object.")

        missing = required_fields - set(case.keys())

        if missing:
            raise ValueError(
                f"Generated test case is missing fields: {sorted(missing)}"
            )

        normalized.append({
            **case,
            "test_case_id": f"TC-{index:02d}",
            "source_section": node.heading,
            "source_version": node.version,
            "source_node_id": node.node_id,
        })

    return normalized, f"openai:{os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}"


# ---------------------------------------------------------
# Generate and persist test cases
# ---------------------------------------------------------

@app.post("/test-cases/generate")
def generate_test_cases(payload: GenerationRequest):
    with SessionLocal() as db:
        selection = db.get(Selection, payload.selection_id)

        if selection is None:
            raise HTTPException(
                status_code=404,
                detail="Selection not found.",
            )

        node = get_node_or_404(
            db,
            selection.version,
            selection.node_id,
        )

        try:
            cases, generator_name = build_llm_test_cases(
                node,
                payload.count,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Test-case generation failed: {exc}",
            ) from exc

        generation_id = hashlib.sha256(
            f"{selection.id}:{now_iso()}".encode("utf-8")
        ).hexdigest()[:16]

        output_path = (
            GENERATION_DIR / f"generation_{generation_id}.json"
        )

        output = {
            "generation_id": generation_id,
            "selection_id": selection.id,
            "source_version": selection.version,
            "source_node_id": node.node_id,
            "source_heading": node.heading,
            "source_content_hash": node.content_hash,
            "generator": generator_name,
            "test_cases": cases,
        }

        try:
            output_path.write_text(
                json.dumps(
                    output,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            generation = Generation(
                selection_id=selection.id,
                version=selection.version,
                output_path=str(
                    output_path.relative_to(BASE_DIR)
                ),
                created_at=now_iso(),
                status="completed",
            )

            db.add(generation)
            db.commit()
            db.refresh(generation)

        except Exception:
            db.rollback()

            if output_path.exists():
                output_path.unlink()

            raise

        return {
            "generation_record_id": generation.id,
            "output": output,
            "storage_path": generation.output_path,
        }


# ---------------------------------------------------------
# Retrieve stored generations
# ---------------------------------------------------------

@app.get("/generations/{generation_id}")
def get_generation(generation_id: int):
    with SessionLocal() as db:
        generation = db.get(Generation, generation_id)

        if generation is None:
            raise HTTPException(
                status_code=404,
                detail="Generation not found.",
            )

        path = BASE_DIR / generation.output_path

        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail="Generation file is missing.",
            )

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not read the saved generation.",
            ) from exc


@app.get("/selections/{selection_id}/generations")
def list_selection_generations(selection_id: int):
    with SessionLocal() as db:
        if db.get(Selection, selection_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Selection not found.",
            )

        generations = db.scalars(
            select(Generation)
            .where(Generation.selection_id == selection_id)
            .order_by(Generation.id.desc())
        ).all()

        return [
            {
                "generation_id": item.id,
                "version": item.version,
                "created_at": item.created_at,
                "status": item.status,
                "output_path": item.output_path,
            }
            for item in generations
        ]
