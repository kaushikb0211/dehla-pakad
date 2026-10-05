"""Card encoding.

A card is an int 0..51: ``suit * 13 + rank`` where rank 0 is the 2 and rank 12
is the ace. Because rank is the low part of the index, a higher card of the
same suit always has a higher int. Sets of cards are 52-bit int bitmasks.
"""

SUITS = ("S", "H", "D", "C")
SUIT_SYMBOLS = ("♠", "♥", "♦", "♣")
SUIT_NAMES = ("Spades", "Hearts", "Diamonds", "Clubs")
RANKS = "23456789TJQKA"
TEN = 8  # rank index of the 10

FULL_DECK = (1 << 52) - 1
SUIT_MASKS = tuple(((1 << 13) - 1) << (13 * s) for s in range(4))
TENS_MASK = sum(1 << (13 * s + TEN) for s in range(4))


def make_card(suit: int, rank: int) -> int:
    return suit * 13 + rank


def suit_of(card: int) -> int:
    return card // 13


def rank_of(card: int) -> int:
    return card % 13


def is_ten(card: int) -> bool:
    return card % 13 == TEN


def card_str(card: int) -> str:
    """'TS' style string; the ten prints as '10'."""
    r = RANKS[card % 13]
    return ("10" if r == "T" else r) + SUIT_SYMBOLS[card // 13]


def parse_card(text: str) -> int:
    """Parse 'AS', 'TH', '10H', 'qd' etc."""
    t = text.strip().upper()
    if t.startswith("10"):
        t = "T" + t[2:]
    if len(t) != 2 or t[0] not in RANKS or t[1] not in SUITS:
        raise ValueError(f"bad card: {text!r}")
    return make_card(SUITS.index(t[1]), RANKS.index(t[0]))


def parse_cards(text: str) -> list[int]:
    return [parse_card(tok) for tok in text.split()]


def mask_of(cards) -> int:
    m = 0
    for c in cards:
        m |= 1 << c
    return m


def cards_of(mask: int) -> list[int]:
    """Cards in a mask, ascending by index."""
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


def mask_str(mask: int) -> str:
    return " ".join(card_str(c) for c in cards_of(mask))
