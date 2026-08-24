"""Paise in, rupees out. Formatting happens at the UI edge and nowhere else."""


def format_inr(paise: int) -> str:
    """Indian digit grouping: 584300 rupees renders 5,84,300 not 584,300."""
    rupees, sub = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{digits}.{sub:02d}" if sub else f"{sign}₹{digits}"


def format_inr_short(paise: int) -> str:
    """Headline numbers: ₹5.84L, ₹1.2Cr. Scoreboard use only."""
    rupees = abs(paise) / 100
    sign = "-" if paise < 0 else ""
    if rupees >= 1_00_00_000:
        return f"{sign}₹{rupees / 1_00_00_000:.2f}Cr"
    if rupees >= 1_00_000:
        return f"{sign}₹{rupees / 1_00_000:.2f}L"
    if rupees >= 1_000:
        return f"{sign}₹{rupees / 1_000:.1f}K"
    return f"{sign}₹{rupees:.0f}"
