import fitz
import re
import hashlib
import uuid
from pathlib import Path

HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_pdf(pdf_path, version):
    doc = fitz.open(pdf_path)
    nodes = []
    stack = []
    current = None
    order = 0

    def add_body(text, page_num):
        nonlocal current
        if not text.strip():
            return
        if current is None:
            current = {
    "node_id": str(uuid.uuid4()),
    "logical_id": "",
    "heading": "Document Introduction",
    "number": "",
    "level": 0,
    "body_text": "",
    "page_start": page_num,
    "page_end": page_num,
    "children": [],
    "parent_id": None,
    "node_type": "section",
    "source_order": 0,
}
            nodes.append(current)
            stack.append(current)

        current["body_text"] += text.strip() + "\n"
        current["page_end"] = page_num

    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if "lines" not in block:
                continue

            for line in block["lines"]:
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()

                if not text:
                    continue

                match = HEADING_RE.match(text)
                max_size = max(s["size"] for s in spans)
                is_bold = any(s["flags"] & 16 for s in spans)

                is_heading = bool(match) and (
                    max_size >= 11 or is_bold
                )

                if is_heading:
                    number = match.group(1)
                    title = match.group(2)
                    level = number.count(".") + 1

                    node = {
                        "node_id": str(uuid.uuid4()),
                        "logical_id": "",
                        "heading": text,
                        "number": number,
                        "level": level,
                        "body_text": "",
                        "page_start": page_num,
                        "page_end": page_num,
                        "children": [],
                        "parent_id": None,
                        "node_type": "section",
                        "source_order": order,
                    }
                    order += 1

                    # Pop nodes at the same or deeper level.
                    while stack and stack[-1]["level"] >= level:
                        stack.pop()

                    # Find the closest existing parent.
                    parent = stack[-1] if stack else None

                    if parent:
                        node["parent_id"] = parent["node_id"]
                        parent["children"].append(node["node_id"])

                    nodes.append(node)
                    stack.append(node)
                    current = node
                else:
                    add_body(text, page_num)

    # Logical IDs are derived from heading paths, not content.
    by_id = {n["node_id"]: n for n in nodes}

    def make_path(node):
        parts = [node["heading"]]
        parent_id = node["parent_id"]
        while parent_id and parent_id in by_id:
            parent = by_id[parent_id]
            parts.append(parent["heading"])
            parent_id = parent["parent_id"]
        return "/".join(reversed(parts))

    for node in nodes:
        node["logical_id"] = sha256(make_path(node))[:20]
        node["content_hash"] = sha256(
            node["heading"] + "\n" + node["body_text"].strip()
        )
        node["version"] = version

    return nodes