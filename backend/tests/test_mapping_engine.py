# The resolution semantics ported from Carson's workbook. Each test pins one of
# the rules the old engine got wrong, so a regression here means the port broke.

import mapping_engine as me
from conftest import patch_mappings, row

IS = "income_statement"


def _spec(master, reclasses=None):
    return {IS: {"master": master, "reclasses": reclasses or []}}


# ---------------------------------------------------------------- resolve_active

def test_lowest_priority_with_data_wins(monkeypatch):
    patch_mappings(monkeypatch, _spec([
        row("Revenue", "revenue", priority=1),
        row("Revenue", "totalRevenue", priority=2),
    ]))
    records = [{"revenue": 100.0, "totalRevenue": 100.0}]
    active = me.resolve_active(IS, records)
    assert [r["synonym"] for r in active["Revenue"]] == ["revenue"]


def test_falls_back_when_priority_1_has_no_data(monkeypatch):
    patch_mappings(monkeypatch, _spec([
        row("Revenue", "revenue", priority=1),
        row("Revenue", "totalRevenue", priority=2),
    ]))
    records = [{"revenue": 0.0, "totalRevenue": 100.0}]
    active = me.resolve_active(IS, records)
    assert [r["synonym"] for r in active["Revenue"]] == ["totalRevenue"]


def test_ties_are_additive(monkeypatch):
    """MINIFS picks the minimum priority and EVERY row at it is active.

    The real case: SG&A falls back from the combined field to
    generalAndAdministrative + sellingAndMarketing TOGETHER. Taking only the
    first of the tie drops half the expense.
    """
    patch_mappings(monkeypatch, _spec([
        row("SG&A", "sellingGeneralAndAdministrative", priority=1),
        row("SG&A", "generalAndAdministrative", priority=2),
        row("SG&A", "sellingAndMarketing", priority=2),
    ]))
    records = [{"sellingGeneralAndAdministrative": 0.0,
                "generalAndAdministrative": 30.0, "sellingAndMarketing": 70.0}]

    active = me.resolve_active(IS, records)
    assert sorted(r["synonym"] for r in active["SG&A"]) == [
        "generalAndAdministrative", "sellingAndMarketing"]
    pulled = me.pull_aliased(IS, records[0], records, {"sga": ["SG&A"]})
    assert pulled["sga"] == 100.0  # summed, not first-of-tie


def test_has_data_is_judged_across_the_whole_history(monkeypatch):
    """The winning synonym is chosen once from all years, then used everywhere.

    revenue is zero in the LATEST year but present earlier, so it still wins —
    per-year selection would silently switch synonyms mid-history.
    """
    patch_mappings(monkeypatch, _spec([
        row("Revenue", "revenue", priority=1),
        row("Revenue", "totalRevenue", priority=2),
    ]))
    latest = {"revenue": 0.0, "totalRevenue": 95.0}
    records = [latest, {"revenue": 90.0, "totalRevenue": 90.0}]

    active = me.resolve_active(IS, records)
    assert [r["synonym"] for r in active["Revenue"]] == ["revenue"]
    # and the latest year reports revenue's actual value (0), not totalRevenue's
    pulled = me.pull_aliased(IS, latest, records, {"rev": ["Revenue"]})
    assert pulled["rev"] == 0.0


def test_check_rows_are_never_pulled(monkeypatch):
    """Total/Check and Check rows are reconcile targets, not model inputs."""
    patch_mappings(monkeypatch, _spec([
        row("Revenue", "revenue", use_type="Total/Check", priority=1),
        row("Revenue", "netIncome", use_type="Check", priority=2),
    ]))
    active = me.resolve_active(IS, [{"revenue": 100.0, "netIncome": 50.0}])
    assert active["Revenue"] == []


def test_backup_rows_are_pullable(monkeypatch):
    patch_mappings(monkeypatch, _spec([
        row("D&A", "depreciationAndAmortization", priority=1),
        row("D&A", "depreciationAmortizationAccretion",
            use_type="Duplicate/Backup", priority=2),
    ]))
    records = [{"depreciationAndAmortization": 0.0,
                "depreciationAmortizationAccretion": 12.0}]
    active = me.resolve_active(IS, records)
    assert [r["synonym"] for r in active["D&A"]] == ["depreciationAmortizationAccretion"]


def test_lines_without_a_priority_1_anchor_still_resolve(monkeypatch):
    """Some lines start at Priority 2 (the CF working-capital splits) because
    Priority 1 is an aggregate on a different model line. The anchor rule only
    applies when an anchor exists."""
    patch_mappings(monkeypatch, _spec([
        row("Change in AR", "changeInReceivables", priority=2),
        row("Change in AR", "receivablesChange", priority=3),
    ]))
    active = me.resolve_active(IS, [{"changeInReceivables": -5.0, "receivablesChange": -5.0}])
    assert [r["synonym"] for r in active["Change in AR"]] == ["changeInReceivables"]


def test_no_candidates_yields_empty_list_not_error(monkeypatch):
    patch_mappings(monkeypatch, _spec([row("Revenue", "revenue", priority=1)]))
    active = me.resolve_active(IS, [{"revenue": 0.0}])
    assert active["Revenue"] == []


# ----------------------------------------------------------- resolve_line_value

def test_reported_target_takes_lowest_priority_outright_no_summing(monkeypatch):
    """A reported total is a single figure — ties never add here."""
    patch_mappings(monkeypatch, _spec([
        row("Net Income (reported)", "netIncomeFromContinuingOperations",
            use_type="Total/Check", priority=1),
        row("Net Income (reported)", "netIncome", use_type="Total/Check", priority=2),
    ]))
    rec = {"netIncomeFromContinuingOperations": 80.0, "netIncome": 75.0}
    value = me.resolve_line_value(IS, "Net Income (reported)", rec, [rec])
    assert value == 80.0  # priority 1 only, never 155


def test_reported_target_is_none_when_nothing_has_data(monkeypatch):
    """None means 'not reported' — callers must be able to tell it from zero."""
    patch_mappings(monkeypatch, _spec([
        row("Net Income (reported)", "netIncome", use_type="Total/Check", priority=1),
    ]))
    rec = {"netIncome": 0.0}
    assert me.resolve_line_value(IS, "Net Income (reported)", rec, [rec]) is None


# ------------------------------------------------- pull_aliased and reclasses

def test_pull_aliased_folds_multiple_model_lines_into_one_key(monkeypatch):
    patch_mappings(monkeypatch, _spec([
        row("Other Current Liabilities", "otherCurrentLiabilities", priority=1),
        row("Deferred Revenue", "deferredRevenue", priority=1),
    ]))
    rec = {"otherCurrentLiabilities": 40.0, "deferredRevenue": 10.0}
    pulled = me.pull_aliased(IS, rec, [rec],
                             {"other_current_liab": ["Other Current Liabilities",
                                                     "Deferred Revenue"]})
    assert pulled["other_current_liab"] == 50.0


def test_reclass_lands_on_its_ticker_year_and_line_only(monkeypatch):
    patch_mappings(monkeypatch, _spec(
        [row("Revenue", "revenue", priority=1)],
        reclasses=[{"ticker": "AAPL", "fiscal_year": 2024,
                    "to_line": "Revenue", "amount": 7.0}],
    ))
    rec = {"revenue": 100.0, "calendarYear": "2024"}
    aliases = {"rev": ["Revenue"]}
    assert me.pull_aliased(IS, rec, [rec], aliases, ticker="AAPL")["rev"] == 107.0
    assert me.pull_aliased(IS, rec, [rec], aliases, ticker="MSFT")["rev"] == 100.0
    other_year = {"revenue": 100.0, "calendarYear": "2023"}
    assert me.pull_aliased(IS, other_year, [other_year], aliases, ticker="AAPL")["rev"] == 100.0


def test_reclasses_on_the_same_target_sum(monkeypatch):
    patch_mappings(monkeypatch, _spec([], reclasses=[
        {"ticker": "HON", "fiscal_year": 2023, "to_line": "SG&A", "amount": 3.0},
        {"ticker": "HON", "fiscal_year": 2023, "to_line": "SG&A", "amount": 4.0},
    ]))
    assert me.reclass_adjustments(IS, "HON") == {(2023, "SG&A"): 7.0}


# ------------------------------------------------------------------ fiscal_year

def test_fiscal_year_prefers_calendar_year():
    assert me.fiscal_year({"calendarYear": "2024", "date": "2023-12-30"}) == 2024


def test_fiscal_year_falls_back_to_date():
    assert me.fiscal_year({"date": "2023-09-30"}) == 2023


def test_fiscal_year_none_when_absent():
    assert me.fiscal_year({}) is None
