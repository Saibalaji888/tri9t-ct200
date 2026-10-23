from app.parser import parse_pdf
from pathlib import Path

PDF = Path("data/ct200_manual.pdf")


def test_parser_returns_nodes():
    nodes = parse_pdf(PDF, 1)
    assert nodes


def test_nodes_have_hashes_and_ids():
    nodes = parse_pdf(PDF, 1)
    for node in nodes:
        assert node["node_id"]
        assert node["logical_id"]
        assert node["content_hash"]


def test_battery_life_heading_is_preserved():
    nodes = parse_pdf(PDF, 1)
    matches = [
        n for n in nodes
        if "Battery Life Under Typical Use" in n["heading"]
    ]
    assert matches
    assert matches[0]["number"] == "2.1.1.1"


def test_error_codes_section_is_preserved():
    nodes = parse_pdf(PDF, 1)
    assert any("4.2 Error Codes" in n["heading"] for n in nodes)


def test_source_order_is_recorded():
    nodes = parse_pdf(PDF, 1)
    order = [n["source_order"] for n in nodes]
    assert order == sorted(order)