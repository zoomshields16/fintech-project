# Test setup shared by every test file.
#
# The tests exercise the engines against small hand-built inputs where the right
# answer is known in advance — unlike the reconcile pipeline, which grades our
# math against FMP's totals, these grade it against arithmetic done by hand.

import sys
from pathlib import Path

# Make `import mapping_engine` etc. work no matter where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mapping_engine  # noqa: E402


def patch_mappings(monkeypatch, statements):
    """Point the mapping engine at a synthetic spec instead of mappings.json.

    `statements` is the same shape as the real file's "statements" key. The
    engine reads the spec through load_mappings(), so replacing that one
    function isolates the resolution rules from Carson's actual data.
    """
    monkeypatch.setattr(mapping_engine, "load_mappings", lambda: {"statements": statements})


def row(model_line, synonym, use_type="Detail", priority=1):
    """A master-map row with only the fields the engine reads."""
    return {"model_line": model_line, "synonym": synonym,
            "use_type": use_type, "priority": priority}
