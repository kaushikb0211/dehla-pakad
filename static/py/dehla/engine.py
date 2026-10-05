"""Dehla Pakad (Mendikot) rules engine.

Seats are 0..3 and play proceeds 0 -> 1 -> 2 -> 3 -> 0. Partners sit
opposite each other, so team 0 = seats {0, 2} and team 1 = seats {1, 3}.
"Dealer's left" is the next seat in play order.

Two variants share this engine and differ only in what happens when a trick
ends:

* ``classic``: the trick winner's team takes the trick (and its 10s).
* ``streak`` ("two in a row"): trick cards pile up in the middle. A player who
  wins two tricks in a row - that player, not just their team - collects the
  whole pile and the count starts over. A different winner breaks the run and
  starts their own. The winner of the last trick takes whatever is left.
"""

from __future__ import annotations

import random

from .cards import FULL_DECK, SUIT_MASKS, TEN, TENS_MASK, card_str, cards_of

PHASE_TRUMP = "trump"  # 5 cards each dealt, waiting for trump declaration
PHASE_PLAY = "play"
PHASE_DONE = "done"

FIRST_PACKET = 5
HAND_SIZE = 13
NUM_TRICKS = 13

CLASSIC = "classic"
STREAK = "streak"
VARIANTS = (CLASSIC, STREAK)


class IllegalMove(Exception):
    pass


def team_of(player: int) -> int:
    return player & 1


def partner_of(player: int) -> int:
    return (player + 2) & 3


def next_player(player: int) -> int:
    return (player + 1) & 3


def trick_winner(trick, trump):
    """Return the (player, card) entry that wins a (possibly partial) trick."""
    best_p, best_c = trick[0]
    led = best_c // 13
    best_is_trump = led == trump
    for p, c in trick[1:]:
        s = c // 13
        if s == trump:
            if not best_is_trump or c > best_c:
                best_p, best_c, best_is_trump = p, c, True
        elif s == led and not best_is_trump and c > best_c:
            best_p, best_c = p, c
    return best_p, best_c


class GameState:
    __slots__ = (
        "hands",       # list[int] bitmask per seat
        "stock",       # cards still to be dealt after trump is declared (deal order)
        "dealer",
        "chooser",     # seat that declares trump (dealer's left)
        "trump",       # suit int or None
        "phase",
        "leader",      # seat that led the current trick
        "turn",        # seat to act
        "trick",       # list[(player, card)] for the trick in progress
        "played",      # bitmask of every card played so far (incl. current trick)
        "voids",       # list[int] 4-bit mask of suits each seat has shown out of
        "tens_won",    # [team0, team1]
        "tens_cards",  # [mask, mask] tens captured per team
        "tricks_won",  # [team0, team1]
        "tricks_played",
        "history",     # list of completed tricks, or None for fast simulations
        "variant",     # CLASSIC or STREAK
        "pot",         # STREAK: mask of cards waiting in the middle
        "pot_tricks",  # STREAK: number of tricks in the pot
        "streak",      # STREAK: seat that won the last trick of the pot, or None
    )

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_deck(cls, deck: list[int], dealer: int = 0, variant: str = CLASSIC) -> "GameState":
        """Deal one card at a time starting at the dealer's left.

        The first 20 cards give 5 to each player; the remaining 32 are held in
        ``stock`` and dealt (continuing the same rotation) after trump.
        """
        if sorted(deck) != list(range(52)):
            raise ValueError("deck must be a permutation of 0..51")
        s = cls.__new__(cls)
        s.hands = [0, 0, 0, 0]
        first = 4 * FIRST_PACKET
        for i, c in enumerate(deck[:first]):
            s.hands[(dealer + 1 + i) & 3] |= 1 << c
        s.stock = list(deck[first:])
        s.dealer = dealer
        s.chooser = next_player(dealer)
        s.trump = None
        s.phase = PHASE_TRUMP
        s.leader = s.chooser
        s.turn = s.chooser
        s.trick = []
        s.played = 0
        s.voids = [0, 0, 0, 0]
        s.tens_won = [0, 0]
        s.tens_cards = [0, 0]
        s.tricks_won = [0, 0]
        s.tricks_played = 0
        s.history = []
        s._init_variant(variant)
        return s

    @classmethod
    def new_game(cls, rng: random.Random | None = None, dealer: int = 0,
                 variant: str = CLASSIC) -> "GameState":
        rng = rng or random.Random()
        deck = list(range(52))
        rng.shuffle(deck)
        return cls.from_deck(deck, dealer, variant)

    @classmethod
    def from_hands(cls, hands, trump: int, leader: int, dealer: int | None = None,
                   variant: str = CLASSIC) -> "GameState":
        """Start directly in the play phase from fully specified 13-card hands."""
        hands = list(hands)
        total = 0
        for h in hands:
            if total & h:
                raise ValueError("hands overlap")
            total |= h
        s = cls.__new__(cls)
        s.hands = hands
        s.stock = []
        s.dealer = (leader - 1) & 3 if dealer is None else dealer
        s.chooser = leader
        s.trump = trump
        s.phase = PHASE_PLAY
        s.leader = leader
        s.turn = leader
        s.trick = []
        s.played = FULL_DECK & ~total
        s.voids = [0, 0, 0, 0]
        s.tens_won = [0, 0]
        s.tens_cards = [0, 0]
        s.tricks_won = [0, 0]
        s.tricks_played = 0
        s.history = []
        s._init_variant(variant)
        return s

    def _init_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}")
        self.variant = variant
        self.pot = 0
        self.pot_tricks = 0
        self.streak = None

    def declare_trump(self, suit: int) -> None:
        if self.phase != PHASE_TRUMP:
            raise IllegalMove("trump already declared")
        if suit not in (0, 1, 2, 3):
            raise IllegalMove(f"bad suit {suit}")
        self.trump = suit
        for i, c in enumerate(self.stock):
            self.hands[(self.dealer + 1 + i) & 3] |= 1 << c
        self.stock = []
        self.phase = PHASE_PLAY
        self.leader = self.turn = self.chooser

    def copy(self, keep_history: bool = False) -> "GameState":
        s = GameState.__new__(GameState)
        s.hands = self.hands[:]
        s.stock = self.stock[:]
        s.dealer = self.dealer
        s.chooser = self.chooser
        s.trump = self.trump
        s.phase = self.phase
        s.leader = self.leader
        s.turn = self.turn
        s.trick = self.trick[:]
        s.played = self.played
        s.voids = self.voids[:]
        s.tens_won = self.tens_won[:]
        s.tens_cards = self.tens_cards[:]
        s.tricks_won = self.tricks_won[:]
        s.tricks_played = self.tricks_played
        s.history = list(self.history) if keep_history and self.history is not None else None
        s.variant = self.variant
        s.pot = self.pot
        s.pot_tricks = self.pot_tricks
        s.streak = self.streak
        return s

    # ------------------------------------------------------------------ play
    @property
    def led_suit(self):
        return self.trick[0][1] // 13 if self.trick else None

    def legal_mask(self) -> int:
        hand = self.hands[self.turn]
        if not self.trick:
            return hand
        follow = hand & SUIT_MASKS[self.trick[0][1] // 13]
        return follow or hand

    def legal_moves(self) -> list[int]:
        return cards_of(self.legal_mask())

    def play(self, card: int, check: bool = True):
        """Play ``card`` for the seat to act. Returns the trick winner when this
        card completes a trick, else None."""
        p = self.turn
        bit = 1 << card
        if check:
            if self.phase != PHASE_PLAY:
                raise IllegalMove(f"cannot play during phase {self.phase!r}")
            if not self.hands[p] & bit:
                raise IllegalMove(f"seat {p} does not hold {card_str(card)}")
            if not self.legal_mask() & bit:
                raise IllegalMove(f"{card_str(card)} does not follow suit")
        trick = self.trick
        if trick:
            led = trick[0][1] // 13
            if card // 13 != led:
                self.voids[p] |= 1 << led
        self.hands[p] ^= bit
        self.played |= bit
        trick.append((p, card))
        if len(trick) < 4:
            self.turn = (p + 1) & 3
            return None

        winner, _ = trick_winner(trick, self.trump)
        team = winner & 1
        cards = 0
        for _, c in trick:
            cards |= 1 << c
        self.tricks_played += 1
        if self.variant == CLASSIC:
            collected, n_tricks = cards, 1
        else:
            self.pot |= cards
            self.pot_tricks += 1
            if self.streak == winner or self.tricks_played == NUM_TRICKS:
                collected, n_tricks = self.pot, self.pot_tricks
                self.pot = self.pot_tricks = 0
                self.streak = None
            else:
                collected, n_tricks = 0, 0
                self.streak = winner
        tmask = collected & TENS_MASK
        if tmask:
            self.tens_cards[team] |= tmask
            self.tens_won[team] += tmask.bit_count()
        self.tricks_won[team] += n_tricks
        if self.history is not None:
            self.history.append({"leader": trick[0][0], "cards": list(trick), "winner": winner,
                                 "collected": collected, "collected_tricks": n_tricks})
        self.trick = []
        self.leader = self.turn = winner
        if self.tricks_played == NUM_TRICKS:
            self.phase = PHASE_DONE
        return winner

    def collects_if_won(self, player: int) -> bool:
        """Would ``player`` take the cards (and 10s) of the current trick by
        winning it? Always true in classic; in streak only for the player on a
        run, or on the last trick."""
        if self.variant == CLASSIC:
            return True
        return self.streak == player or self.tricks_played == NUM_TRICKS - 1

    def stake_tens(self) -> int:
        """Mask of 10s that the current trick decides: those in the trick, plus
        in streak those waiting in the pot."""
        m = 0
        for _, c in self.trick:
            if c % 13 == TEN:
                m |= 1 << c
        if self.variant == STREAK:
            m |= self.pot & TENS_MASK
        return m

    # ------------------------------------------------------------------ result
    def decided(self) -> bool:
        """True once the outcome can no longer change (a team has 3 tens, or 2-2)."""
        a, b = self.tens_won
        return a >= 3 or b >= 3 or (a == 2 and b == 2)

    def all_tens_taken(self) -> bool:
        """True once all four 10s are captured; nothing after that can matter."""
        return self.tens_won[0] + self.tens_won[1] == 4

    def score(self, team: int) -> float:
        """Outcome plus a small bonus per 10 of margin, so 4-0 beats 3-1 and 3-1
        beats losing; the bonus never outweighs win/tie/loss."""
        return self.outcome(team) + 0.1 * (self.tens_won[team] - self.tens_won[team ^ 1])

    def outcome(self, team: int) -> int:
        """+1 win, 0 tie, -1 loss for ``team``. Only meaningful once decided()."""
        mine, theirs = self.tens_won[team], self.tens_won[team ^ 1]
        if mine >= 3:
            return 1
        if theirs >= 3:
            return -1
        return 0

    def hand_size(self, player: int) -> int:
        return self.hands[player].bit_count()

    def unseen_by(self, player: int) -> int:
        """Cards ``player`` cannot see: not in their hand and not yet played."""
        return FULL_DECK & ~self.played & ~self.hands[player]

    def __repr__(self) -> str:
        from .cards import mask_str
        lines = [f"GameState(phase={self.phase}, trump={self.trump}, turn={self.turn}, tens={self.tens_won})"]
        for p in range(4):
            lines.append(f"  {p}: {mask_str(self.hands[p])}")
        if self.trick:
            lines.append("  trick: " + " ".join(f"{p}:{card_str(c)}" for p, c in self.trick))
        return "\n".join(lines)


__all__ = [
    "GameState", "IllegalMove", "trick_winner", "team_of", "partner_of", "next_player",
    "PHASE_TRUMP", "PHASE_PLAY", "PHASE_DONE", "TENS_MASK", "CLASSIC", "STREAK", "VARIANTS",
]
