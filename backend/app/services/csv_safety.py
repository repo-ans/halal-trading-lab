"""CSV formula-injection safety (Stage F plan point 4). Two distinct rules for
two distinct kinds of cell -- conflating them would either leave free text
unescaped (an injection vector) or wrongly escape a legitimate negative number
(a minus sign is one of the trigger characters)."""
import re

_DANGEROUS_LEADING_CHARS = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")


def csv_safe(value: str) -> str:
    """For FREE-TEXT cells only (instrument symbol/name, rejection message).
    Prefixes a single quote if the value starts with a formula-injection
    trigger character -- spreadsheet software treats a leading `'` as "force
    text" and never evaluates the rest of the cell as a formula."""
    if value and value[0] in _DANGEROUS_LEADING_CHARS:
        return "'" + value
    return value


def numeric_cell(value: str) -> str:
    """For NUMERIC cells only (qty, prices, cash balances -- anything that is a
    plain decimal number as a string, negatives included). Asserts the value
    actually matches a bare number, rather than silently passing through
    something that isn't one -- a numeric column with a surprise non-numeric
    value is a bug to surface, not a string to escape."""
    if not _NUMERIC_PATTERN.match(value):
        raise ValueError(f"expected a plain numeric string, got {value!r}")
    return value
