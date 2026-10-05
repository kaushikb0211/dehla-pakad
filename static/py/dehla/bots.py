"""Baseline bots.

Bot contract: a bot may read ``state.hands[player]`` (its own hand) and public
information only (trick, played, voids, trump, tens/tricks won, hand sizes).
It must never look at another seat's hand.
"""

from __future__ import annotations

import random

from .cards import FULL_DECK, SUIT_MASKS, TEN, TENS_MASK, cards_of
from .engine import STREAK, GameState, trick_winner


class Bot:
    name = "bot"

    def choose_trump(self, state: GameState, player: int) -> int:
        raise NotImplementedError

    def choose_card(self, state: GameState, player: int) -> int:
        raise NotImplementedError


class RandomBot(Bot):
    name = "random"

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def choose_trump(self, state, player):
        return self.rng.randrange(4)

    def choose_card(self, state, player):
        return self.rng.choice(state.legal_moves())


# --------------------------------------------------------------------- helpers
def _above(card: int) -> int:
    """Mask of same-suit cards ranked above ``card``."""
    return SUIT_MASKS[card // 13] & ~((2 << card) - 1)


def is_boss(card: int, known: int) -> bool:
    """True if every higher card of the suit is already played or in ``known``."""
    return not (_above(card) & ~known)


def overtake_helps(state: GameState, player: int, card: int) -> bool:
    """Partner is winning the trick. Would playing ``card`` over them stop an
    opponent still to play from taking it?

    True only if some card ``player`` can't see (and that a later opponent
    isn't known to be void in) beats partner's card but not ``card``. E.g.
    partner's Q under my K: nothing lies between them, so the K is wasted.
    """
    trick = state.trick
    trump = state.trump
    later_opps = [(player + k) & 3 for k in range(1, 4 - len(trick))
                  if (player + k) & 1 != player & 1]
    if not later_opps:
        return False
    with_me = trick + [(player, card)]
    unseen = FULL_DECK & ~state.played & ~state.hands[player]
    for u in cards_of(unseen):
        if all(state.voids[q] >> (u // 13) & 1 for q in later_opps):
            continue
        if (trick_winner(trick + [(-1, u)], trump)[0] == -1
                and trick_winner(with_me + [(-1, u)], trump)[0] != -1):
            return True
    return False


def top_trump(state: GameState, player: int, hand: int):
    """The highest trump left in the game, if it is in ``hand``; else None.
    Led, it cannot lose."""
    trumps = hand & SUIT_MASKS[state.trump]
    if not trumps:
        return None
    top = trumps.bit_length() - 1
    return top if is_boss(top, state.played | state.hands[player]) else None


def has_sure_winner(state: GameState, player: int, hand: int) -> bool:
    """Does ``hand`` hold a card that should win a trick it leads: the top
    remaining trump, or the top card of a side suit nobody has shown out of?"""
    known = state.played | state.hands[player]
    trump = state.trump
    for s in range(4):
        cards = hand & SUIT_MASKS[s]
        if not cards:
            continue
        top = cards.bit_length() - 1
        if not is_boss(top, known):
            continue
        if s == trump or not any(state.voids[q] >> s & 1 for q in range(4) if q != player):
            return True
    return False


def _lowest_by_rank(mask: int, hand: int) -> int:
    """Lowest-ranked card in mask; ties go to the shortest suit in hand."""
    return min(cards_of(mask), key=lambda c: (c % 13, (hand & SUIT_MASKS[c // 13]).bit_count()))


def _lowest_non_ten(mask: int, hand: int) -> int:
    non_ten = mask & ~TENS_MASK
    return _lowest_by_rank(non_ten or mask, hand)


class HeuristicBot(Bot):
    """Simple rule-based player; also used as the rollout policy for PIMC."""

    name = "heuristic"

    #: Two-in-a-row behaviours (each can be switched off to measure it).
    STREAK_TWEAKS = frozenset({"no_overtake", "run_lead", "ten_gate", "top_trump_ten"})

    def __init__(self, tweaks=None):
        self.tweaks = self.STREAK_TWEAKS if tweaks is None else frozenset(tweaks)

    # -- trump ------------------------------------------------------------
    def choose_trump(self, state, player):
        hand = state.hands[player]
        best, best_score = 0, -1.0
        for s in range(4):
            cards = cards_of(hand & SUIT_MASKS[s])
            score = 3.0 * len(cards) + sum(max(0, c % 13 - TEN) for c in cards)
            if score > best_score:
                best, best_score = s, score
        return best

    # -- play -------------------------------------------------------------
    def choose_card(self, state, player):
        hand = state.hands[player]
        trick = state.trick
        trump = state.trump
        known = state.played | hand

        if not trick:
            return self._lead(state, player, hand, known)

        led = trick[0][1] // 13
        wp, wc = trick_winner(trick, trump)
        partner = (player + 2) & 3
        partner_winning = wp == partner
        last = len(trick) == 3
        # Is anything riding on this trick? Its 10s - and in the two-in-a-row
        # variant also the 10s waiting in the pot, and an opponent who would
        # collect the pot by winning it.
        trick_has_ten = bool(state.stake_tens()) or bool(
            state.pot and (wp & 1) != (player & 1) and state.collects_if_won(wp))
        safe = partner_winning and (last or self._holds(state, player, wc, known))
        # Does a 10 dropped into this trick get banked by its winner? (Always
        # in classic; in two-in-a-row only if that winner collects.)
        partner_banks = state.collects_if_won(partner)
        i_bank = state.collects_if_won(player)
        # Two in a row: a partner who is winning this trick on a run is about
        # to collect the pile - never overtake them, that would break the run.
        partner_collects = ("no_overtake" in self.tweaks and state.variant == STREAK
                            and partner_winning and state.collects_if_won(partner))

        follow = hand & SUIT_MASKS[led]
        if follow:
            if wc // 13 != led:
                # Someone has trumped; following suit cannot win.
                if safe and partner_banks:
                    return self._ten_or_lowest(follow, hand)
                return _lowest_non_ten(follow, hand)
            if safe:
                return self._ten_or_lowest(follow, hand) if partner_banks else _lowest_non_ten(follow, hand)
            beat = follow & _above(wc)
            if beat and partner_winning and trick_has_ten and not partner_collects:
                # Partner's trick holds a 10 but could still be overtaken: cover
                # it, but only with a card that actually shuts an opponent out.
                useful = [c for c in cards_of(beat) if overtake_helps(state, player, c)]
                for c in useful:
                    if is_boss(c, known):
                        return c
                if useful:
                    return useful[-1]
            if beat and not partner_winning:
                if last:
                    tens = beat & TENS_MASK
                    # Winning with our own 10 secures it - straight away, or in
                    # two-in-a-row by leading the top trump next to collect.
                    if tens and (i_bank or ("top_trump_ten" in self.tweaks
                                            and top_trump(state, player, hand & ~tens) is not None)):
                        return cards_of(tens)[0]
                    if trick_has_ten:
                        return cards_of(beat)[0]
                    # Nothing worth taking: keep the high cards for later.
                    return _lowest_non_ten(follow, hand)
                for c in cards_of(beat):  # cheapest card that is now the boss
                    if is_boss(c, known):
                        return c
                if trick_has_ten:
                    return cards_of(beat)[-1]
            return _lowest_non_ten(follow, hand)

        # Void in the led suit.
        trumps = hand & SUIT_MASKS[trump] if trump is not None else 0
        if safe:
            return self._discard_ten_or_low(hand, trump) if partner_banks else self._discard(hand, trump)
        if partner_winning and trick_has_ten and trumps and wc // 13 != trump and not partner_collects:
            # Partner's 10-bearing trick is at risk: ruff to protect it.
            return _lowest_non_ten(trumps, hand)
        if not partner_winning and trumps:
            over = trumps & _above(wc) if wc // 13 == trump else trumps
            if over:
                if last:
                    tens = over & TENS_MASK
                    if tens and (i_bank or ("top_trump_ten" in self.tweaks
                                            and top_trump(state, player, hand & ~tens) is not None)):
                        return cards_of(tens)[0]
                    if not trick_has_ten:  # don't spend a trump on an empty trick
                        return self._discard(hand, trump)
                return _lowest_non_ten(over, hand) if (over & ~TENS_MASK) else cards_of(over)[0]
        return self._discard(hand, trump)

    @staticmethod
    def _holds(state, player, card, known) -> bool:
        """Will ``card`` (currently winning) hold against seats still to play?"""
        if not is_boss(card, known):
            return False
        trump = state.trump
        suit = card // 13
        if suit == trump:
            return True
        n_left = 3 - len(state.trick)
        for k in range(1, n_left + 1):
            q = (player + k) & 3
            if q & 1 == player & 1:
                continue
            v = state.voids[q]
            if v >> suit & 1 and not (v >> trump & 1):
                return False  # opponent may ruff
        return True

    @staticmethod
    def _ten_or_lowest(mask, hand):
        tens = mask & TENS_MASK
        return cards_of(tens)[0] if tens else _lowest_by_rank(mask, hand)

    @staticmethod
    def _discard(hand, trump):
        tmask = SUIT_MASKS[trump] if trump is not None else 0
        side = hand & ~tmask
        if side & ~TENS_MASK:
            return _lowest_by_rank(side & ~TENS_MASK, hand)
        if hand & tmask & ~TENS_MASK:
            return _lowest_by_rank(hand & tmask & ~TENS_MASK, hand)
        return _lowest_by_rank(hand, hand)

    def _discard_ten_or_low(self, hand, trump):
        tmask = SUIT_MASKS[trump] if trump is not None else 0
        side_tens = hand & TENS_MASK & ~tmask
        if side_tens:
            return cards_of(side_tens)[0]
        if hand & TENS_MASK:
            return cards_of(hand & TENS_MASK)[0]
        return self._discard(hand, trump)

    def _lead(self, state, player, hand, known):
        trump = state.trump
        opps = ((player + 1) & 3, (player + 3) & 3)
        partner = (player + 2) & 3

        def suit_safe(s):
            for q in opps:
                v = state.voids[q]
                if v >> s & 1 and not (v >> trump & 1):
                    return False
            return True

        side = [s for s in range(4) if s != trump and hand & SUIT_MASKS[s]]
        streak = state.variant == STREAK
        if streak and "run_lead" in self.tweaks and state.streak == player and state.pot & TENS_MASK:
            # 0. On a run with 10s in the pile: lead a sure winner to collect
            #    it - the top trump, or the top card of a suit nobody has shown
            #    out of. (Not for a pile without 10s: that wastes the winner.)
            trumps = hand & SUIT_MASKS[trump]
            if trumps and is_boss(trumps.bit_length() - 1, known):
                return trumps.bit_length() - 1
            for s in side:
                top = (hand & SUIT_MASKS[s]).bit_length() - 1
                if is_boss(top, known) and not any(state.voids[q] >> s & 1 for q in range(4) if q != player):
                    return top
        # 1. Cash a winning non-ten card.
        for s in side:
            if not suit_safe(s):
                continue
            top = (hand & SUIT_MASKS[s]).bit_length() - 1
            if top % 13 != TEN and is_boss(top, known):
                return top
        # 2. Cash a ten that has become the boss. (Two in a row: winning only
        #    banks it on a collecting trick, or with a sure winner to follow.)
        for s in side:
            t = s * 13 + TEN
            if hand >> t & 1 and is_boss(t, known) and suit_safe(s):
                if (not streak or "ten_gate" not in self.tweaks or state.collects_if_won(player)
                        or has_sure_winner(state, player, hand & ~(1 << t))):
                    return t
        # 3. Lead into a suit partner can ruff.
        pv = state.voids[partner]
        for s in side:
            if pv >> s & 1 and not (pv >> trump & 1) and hand & SUIT_MASKS[s] & ~TENS_MASK:
                return _lowest_by_rank(hand & SUIT_MASKS[s] & ~TENS_MASK, hand)
        # 4. Low card from the longest side suit.
        side_low = [s for s in side if hand & SUIT_MASKS[s] & ~TENS_MASK]
        if side_low:
            s = max(side_low, key=lambda s: ((hand & SUIT_MASKS[s]).bit_count(), -s))
            return _lowest_by_rank(hand & SUIT_MASKS[s] & ~TENS_MASK, hand)
        # 5. Only trumps / tens left.
        trumps = hand & SUIT_MASKS[trump]
        if trumps:
            top = trumps.bit_length() - 1
            if is_boss(top, known):
                return top
            return _lowest_non_ten(trumps, hand)
        return _lowest_by_rank(hand, hand)
