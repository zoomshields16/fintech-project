def find_value_first_match(record, synonyms):
    """Try each synonym in order; return the first match. Used by income statement."""
    for name in synonyms:
        if name in record and record[name] is not None:
            return record[name]
    return None


def find_value_priority(record, priority_synonyms):
    """
    0/1/2 priority matching — prevents double-counting when companies combine line items.
    0 = unconditional single source (no conflict risk, e.g. Revenue)
    1 = preferred source; if a value is found here, use it
    2 = fallback; only used when no priority-1 field returns a value
    Each entry in priority_synonyms is a (priority, fmp_field_name) tuple.
    """
    for p, field in priority_synonyms:
        if p == 1 and field in record and record[field] is not None:
            return record[field]
    for p, field in priority_synonyms:
        if p in (0, 2) and field in record and record[field] is not None:
            return record[field]
    return None


def pull_accounts_first_match(record, account_map):
    """Pull all accounts via first-match. account_map = {line_name: [synonym, ...]}"""
    return {line: find_value_first_match(record, synonyms) for line, synonyms in account_map.items()}


def pull_accounts_priority(record, account_map):
    """Pull all accounts via 0/1/2 priority. account_map = {line_name: [(priority, field), ...]}"""
    return {line: find_value_priority(record, synonyms) for line, synonyms in account_map.items()}
