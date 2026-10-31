#!/usr/bin/env python3
"""Unit tests for the search-filter logic in generate-dashboard.py.

Run: python3 scripts/test_search_filters.py   (no deps, no network)
"""
import importlib.util
import json
import tempfile
from pathlib import Path

# Load the hyphenated module by path.
_spec = importlib.util.spec_from_file_location(
    "gen_dashboard", Path(__file__).with_name("generate-dashboard.py"))
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

# A cut-down accession_schema.py, in the extension's shape: question -> (text, options, tooltip),
# where "" options means a free-text answer.
SCHEMA_SOURCE = '''
"""Accession questionnaire schema."""
SECTION_TITLES = {0: "Subject Type"}
FORM_QUESTIONS = {
    "otherSubjectDescription": ("Please describe the subject.", "", ""),
    "subjectType": ("What is the subject type?", ["Biological specimen", "Other"], ""),
    "species": ("What is your specimen's species?", "", "Enter a genus and species."),
    "modality": ("What is the modality?", ["Micro CT (or synchrotron)", "MRI"], ""),
    "redistributionAcknowledgement": ("Acknowledgement:", ["I have the right."], ""),
    "license": ("Choose a license:", ["CC BY 4.0", "CC0 1.0"], ""),
    "repoType": ("Lifespan?", ["Archival (long-term)", "Short-term"], ""),
}
DEFAULTS = {}
'''


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


def test_parse_form_questions():
    questions = gd.parse_form_questions(SCHEMA_SOURCE)
    check("questions read in order",
          list(questions)[:3] == ["otherSubjectDescription", "subjectType", "species"])
    check("options kept", questions["modality"][1] == ["Micro CT (or synchrotron)", "MRI"])
    try:
        gd.parse_form_questions("X = 1\n")
        check("missing FORM_QUESTIONS raises", False)
    except ValueError:
        check("missing FORM_QUESTIONS raises", True)
    try:
        gd.parse_form_questions("FORM_QUESTIONS = dict(a=open('x'))\n")
        check("non-literal schema is refused, not executed", False)
    except ValueError:
        check("non-literal schema is refused, not executed", True)


def test_build_search_filters():
    filters = gd.build_search_filters(gd.parse_form_questions(SCHEMA_SOURCE))
    keys = [f["key"] for f in filters]
    check("only multiple-choice questions, in questionnaire order",
          keys == ["subjectType", "modality", "license"])
    check("free-text questions never become filters",
          "species" not in keys and "otherSubjectDescription" not in keys)
    check("ignored questions dropped",
          "repoType" not in keys and "redistributionAcknowledgement" not in keys)
    by_key = {f["key"]: f for f in filters}
    check("short label used", by_key["subjectType"]["label"] == "Subject type")
    check("options carried", by_key["license"]["options"] == ["CC BY 4.0", "CC0 1.0"])


def test_load_falls_back_to_previous():
    previous = [{"key": "modality", "label": "Modality", "options": ["MRI"]}]
    saved_url = gd.ACCESSION_SCHEMA_URL
    gd.ACCESSION_SCHEMA_URL = "file:///nonexistent/accession_schema.py"
    try:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dashboard-data.json"
            path.write_text(json.dumps({"searchFilters": previous}))
            check("previous filters kept when the fetch fails",
                  gd.load_search_filters(path) == previous)
            check("None when the fetch fails and there is no previous file",
                  gd.load_search_filters(Path(d) / "missing.json") is None)
    finally:
        gd.ACCESSION_SCHEMA_URL = saved_url


if __name__ == "__main__":
    print("test_parse_form_questions"); test_parse_form_questions()
    print("test_build_search_filters"); test_build_search_filters()
    print("test_load_falls_back_to_previous"); test_load_falls_back_to_previous()
    print("\nAll search-filter tests passed.")
