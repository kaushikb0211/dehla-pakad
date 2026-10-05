"""Perfect-Information Monte Carlo (PIMC) agent.

Card play: repeatedly *determinize* the hidden information (deal the unseen
cards to the other seats consistent with hand sizes and known voids), then for
every legal card play it out with a rollout policy and average the result.

Trump: with only the 5 cards seen, deal the 47 unseen cards randomly, and for
every suit simulate the full hand with that suit as trump (common random
numbers across suits), then choose the suit with the best average result.
"""

from __future__ import annotations

import os
import random
import time

from .bots import Bot, HeuristicBot, has_sure_winner, is_boss, overtake_helps, top_trump
from .cards import FULL_DECK, SUIT_MASKS, TENS_MASK, cards_of
from .cards import TEN
from .engine import CLASSIC, PHASE_PLAY, GameState, trick_winner


class SamplingError(Exception):
    pass


def sample_world(state: GameState, player: int, rng: random.Random, max_tries: int = 200) -> GameState:
    """Return a copy of ``state`` whose hidden hands are re-dealt at random,
    consistent with everything ``player`` knows.

    Only public info and ``player``'s own hand are read: the unseen card pool
    is derived from ``played``, and the other hands' sizes are public.
    """
    mine = state.hands[player]
    hidden = cards_of(FULL_DECK & ~state.played & ~mine)
    others = [p for p in range(4) if p != player]
    sizes = {p: state.hand_size(p) for p in others}
    if sum(sizes.values()) != len(hidden):
        raise SamplingError("hand sizes do not match unseen cards")
    voids = state.voids
    eligible = [[p for p in others if not (voids[p] >> s & 1)] for s in range(4)]

    for _ in range(max_tries):
        rng.shuffle(hidden)
        # Most-constrained suits first; stable sort keeps the shuffle within a group.
        hidden.sort(key=lambda c: len(eligible[c // 13]))
        need = dict(sizes)
        new = {p: 0 for p in others}
        ok = True
        for c in hidden:
            cands = [p for p in eligible[c // 13] if need[p]]
            if not cands:
                ok = False
                break
            if len(cands) == 1:
                q = cands[0]
            else:
                r = rng.randrange(sum(need[p] for p in cands))
                for q in cands:
                    r -= need[q]
                    if r < 0:
                        break
            new[q] |= 1 << c
            need[q] -= 1
        if ok:
            world = state.copy()
            for p in others:
                world.hands[p] = new[p]
            return world
    raise SamplingError("could not find a deal consistent with known voids")


def rollout(state: GameState, policy: Bot) -> None:
    """Play ``state`` forward in place until all four 10s are captured."""
    while not state.all_tens_taken() and state.phase == PHASE_PLAY:
        state.play(policy.choose_card(state, state.turn), check=False)


def pimc_evaluate(state: GameState, player: int, legal: list[int], *, samples=None,
                  time_limit=None, seed=None, policy: Bot | None = None, with_cross=False):
    """Return (sums, n_worlds) where sums[i] is the total outcome of legal[i].

    With ``with_cross`` also return ``cross``, a flat k*k list of
    sum(score_i * score_j) over worlds, from which the uncertainty of every
    pairwise comparison can be computed (see ``tied_with_best``)."""
    if samples is None and time_limit is None:
        samples = 100
    rng = random.Random(seed)
    policy = policy or HeuristicBot()
    team = player & 1
    k = len(legal)
    sums = [0.0] * k
    cross = [0.0] * (k * k)
    n = 0
    deadline = time.perf_counter() + time_limit if time_limit else None
    while True:
        if samples is not None and n >= samples:
            break
        if deadline is not None and n > 0 and time.perf_counter() >= deadline:
            break
        world = sample_world(state, player, rng)
        scores = []
        for card in legal:
            sim = world.copy()
            sim.play(card, check=False)
            rollout(sim, policy)
            scores.append(sim.score(team))
        for i, a in enumerate(scores):
            sums[i] += a
            if with_cross:
                row = i * k
                for j, b in enumerate(scores):
                    cross[row + j] += a * b
        n += 1
    return (sums, n, cross) if with_cross else (sums, n)


def tied_with_best(sums, n, cross, z=2.0, floor=0.005):
    """Indices whose mean is within ``z`` standard errors (of the paired
    difference, since every move is scored on the same worlds) of the best."""
    k = len(sums)
    means = [x / n for x in sums]
    b = max(range(k), key=means.__getitem__)
    tied = []
    for i in range(k):
        d = means[b] - means[i]
        var = (cross[b * k + b] - 2 * cross[b * k + i] + cross[i * k + i]) / n - d * d
        se = (max(var, 0.0) / n) ** 0.5
        if d <= max(floor, z * se):
            tied.append(i)
    return tied


def trump_evaluate(state: GameState, player: int, *, samples=None, time_limit=None,
                   seed=None, policy: Bot | None = None):
    """Return (sums[4], n) of simulated outcomes for each trump suit."""
    if samples is None and time_limit is None:
        samples = 400
    rng = random.Random(seed)
    policy = policy or HeuristicBot()
    team = player & 1
    mine = state.hands[player]
    unseen = cards_of(FULL_DECK & ~mine)
    extra = 13 - mine.bit_count()
    others = [p for p in range(4) if p != player]
    sums = [0.0] * 4
    n = 0
    deadline = time.perf_counter() + time_limit if time_limit else None
    while True:
        if samples is not None and n >= samples:
            break
        if deadline is not None and n > 0 and time.perf_counter() >= deadline:
            break
        rng.shuffle(unseen)
        hands = [0, 0, 0, 0]
        hands[player] = mine
        for c in unseen[:extra]:
            hands[player] |= 1 << c
        pos = extra
        for p in others:
            for c in unseen[pos:pos + 13]:
                hands[p] |= 1 << c
            pos += 13
        for s in range(4):
            sim = GameState.from_hands(hands, trump=s, leader=state.chooser, dealer=state.dealer,
                                       variant=state.variant)
            sim.history = None
            rollout(sim, policy)
            sums[s] += sim.score(team)
        n += 1
    return sums, n


# Worker entry points (top-level so they pickle for ProcessPoolExecutor).
def _pimc_job(args):
    state, player, legal, samples, time_limit, seed = args
    return pimc_evaluate(state, player, legal, samples=samples, time_limit=time_limit, seed=seed,
                         with_cross=True)


def _trump_job(args):
    state, player, samples, time_limit, seed = args
    return trump_evaluate(state, player, samples=samples, time_limit=time_limit, seed=seed)


class MCBot(Bot):
    """PIMC agent.

    Budget is either ``samples`` (worlds per decision; deterministic, used by
    the arena) or ``time_limit`` seconds. With an ``executor`` the work is
    split across processes, each running the full budget with its own seed.

    ``conserve``: skip moves that spend a high card for nothing (see
    ``conserving_moves``). In duplicate matches this made no measurable
    difference to results, and it avoids moves that look wasteful to a human.
    """

    name = "mc"

    def __init__(self, samples=None, time_limit=None, trump_samples=None, trump_time=None,
                 seed=None, executor=None, workers=None, conserve=True):
        if samples is None and time_limit is None:
            samples = 100
        self.samples = samples
        self.time_limit = time_limit
        self.trump_samples = trump_samples if trump_samples is not None else (
            None if trump_time is not None or time_limit is not None else 4 * (samples or 100))
        self.trump_time = trump_time if trump_time is not None else (time_limit if self.trump_samples is None else None)
        self.rng = random.Random(seed)
        self.executor = executor
        self.workers = workers or (os.cpu_count() or 1)
        self.policy = HeuristicBot()
        self.conserve = conserve
        self.last_eval = None  # {card: mean} from the most recent decision

    def _run(self, job, make_args):
        if self.executor is None:
            return job(make_args(self.rng.randrange(2**31)))
        futures = [self.executor.submit(job, make_args(self.rng.randrange(2**31)))
                   for _ in range(self.workers)]
        results = [f.result() for f in futures]
        merged = [[sum(col) for col in zip(*(r[0] for r in results))], sum(r[1] for r in results)]
        if len(results[0]) > 2:
            merged.append([sum(col) for col in zip(*(r[2] for r in results))])
        return tuple(merged)

    #: Two in a row: also win with a 10 now if the top card of a side suit in
    #: hand will win the next trick with at least this chance (None = off).
    #: Tested at 95% and 90%: neither changed strength measurably (+0.001,
    #: -0.006 over 1000 hands); 95% is the cautious choice.
    SIDE_SUIT_ODDS = 0.95

    #: Never lead a 10 that isn't the top card left in its suit (unless every
    #: card in hand is such a 10). Measured neutral (classic +0.004, two in a
    #: row -0.006 over 1000 hands each); avoids leads that look reckless.
    NO_RISKY_TEN_LEAD = True

    def risky_ten_leads_removed(self, state: GameState, player: int, legal: list[int]) -> list[int]:
        if not self.NO_RISKY_TEN_LEAD or state.trick:
            return legal
        known = state.played | state.hands[player]
        keep = [c for c in legal if c % 13 != TEN or is_boss(c, known)]
        return keep or legal

    def sure_ten(self, state: GameState, player: int, legal: list[int]):
        """Last to play and holding a legal 10 that leaves our team winning the
        trick: playing it banks the 10 with no risk, so it is always right."""
        trick = state.trick
        if len(trick) != 3:
            return None
        for c in legal:
            if c % 13 != TEN:
                continue
            w = trick_winner(trick + [(player, c)], state.trump)[0]
            # In two-in-a-row the 10 is only banked if that winner collects...
            if w & 1 == player & 1 and state.collects_if_won(w):
                return c
            # ...or if we win it ourselves and hold the top trump: leading that
            # next trick is a certain second win, so the pile (and 10) is ours.
            rest = state.hands[player] & ~(1 << c)
            if w == player and top_trump(state, player, rest) is not None:
                return c
            # ...or a side suit's top card that everyone will very likely
            # still have to follow next trick.
            if (w == player and self.SIDE_SUIT_ODDS is not None and state.variant != CLASSIC
                    and self.side_winner_odds(state, player, rest) >= self.SIDE_SUIT_ODDS):
                return c
        return None

    def side_winner_odds(self, state: GameState, player: int, hand: int, n: int = 400) -> float:
        """Chance (over the AI's own imagined deals) that leading the top card
        of some side suit in ``hand`` wins the next trick because every other
        player still holds that suit and must follow. Best suit counts."""
        known = state.played | state.hands[player]
        suits = []
        for s in range(4):
            cards = hand & SUIT_MASKS[s]
            if s != state.trump and cards and is_boss(cards.bit_length() - 1, known):
                if not any(state.voids[q] >> s & 1 for q in range(4) if q != player):
                    suits.append(s)
        if not suits:
            return 0.0
        hits = [0] * len(suits)
        for _ in range(n):
            w = sample_world(state, player, self.rng)
            for i, s in enumerate(suits):
                hits[i] += all(w.hands[q] & SUIT_MASKS[s] for q in range(4) if q != player)
        return max(hits) / n

    @staticmethod
    def sure_collect(state: GameState, player: int):
        """Two in a row: leading while on a run, with 10s waiting in the pile,
        and holding the top trump - lead it; it cannot lose, so the pile is
        collected for certain."""
        if (state.variant == CLASSIC or state.trick or state.streak != player
                or not state.pot & TENS_MASK):
            return None
        return top_trump(state, player, state.hands[player])

    @staticmethod
    def conserving_moves(state: GameState, player: int, legal: list[int]) -> list[int]:
        """Drop moves that spend a high card for nothing (unless that leaves none):

        * partner is winning: don't overtake them unless it shuts out a later
          opponent (see ``overtake_helps``). Dropping a 10 on them is always fine.
        * last to a trick with no 10 that the opponents are winning: don't take
          it, provided a spare (non-10) card can lose it. Losing it with our own
          10 would hand that 10 over, so then all moves stay in.
        """
        trick = state.trick
        if state.variant != CLASSIC:
            return MCBot._streak_moves(state, player, legal)
        if not trick:
            return legal
        trump = state.trump
        wins = lambda c: trick_winner(trick + [(player, c)], trump)[0] == player
        if trick_winner(trick, trump)[0] == (player + 2) & 3:
            keep = [c for c in legal if not wins(c) or c % 13 == TEN or overtake_helps(state, player, c)]
            return keep or legal
        if len(trick) == 3 and not any(c % 13 == TEN for _, c in trick):
            return [c for c in legal if not wins(c) and c % 13 != TEN] or legal
        return legal

    @staticmethod
    def _streak_moves(state: GameState, player: int, legal: list[int]) -> list[int]:
        """Two in a row: don't win a trick with a 10 unless that trick is
        collected, or we hold a sure winner to take the next trick too -
        otherwise the 10 just waits in the pile for someone else's run.
        (The classic shortcuts don't apply here: who wins a trick decides who
        is on a run, so the search weighs everything else.)"""
        if state.collects_if_won(player):
            return legal
        hand = state.hands[player]
        trick = state.trick
        keep = []
        for c in legal:
            if c % 13 == TEN:
                wins = trick_winner(trick + [(player, c)], state.trump)[0] == player
                if wins and not has_sure_winner(state, player, hand & ~(1 << c)):
                    continue
            keep.append(c)
        return keep or legal

    def evaluate_cards(self, state: GameState, player: int, legal=None) -> dict[int, float]:
        legal = legal if legal is not None else state.legal_moves()
        if len(legal) == 1:
            return {legal[0]: 0.0}
        sums, n, cross = self._run(_pimc_job, lambda seed: (state.copy(), player, legal,
                                                            self.samples, self.time_limit, seed))
        self._stats = (legal, sums, n, cross)
        return {c: s / n for c, s in zip(legal, sums)}

    def apply_rules(self, state: GameState, player: int, legal: list[int]):
        """The play rules on top of the search. Returns (forced_card, allowed):
        a card the rules dictate outright, or None and the moves to search."""
        if not self.conserve:
            return None, legal
        ten = self.sure_ten(state, player, legal)
        if ten is not None:
            return ten, [ten]
        sure = self.sure_collect(state, player)
        if sure is not None:
            return sure, [sure]
        legal = self.conserving_moves(state, player, legal)
        legal = self.risky_ten_leads_removed(state, player, legal)
        return (legal[0] if len(legal) == 1 else None), legal

    def choose_card(self, state, player):
        legal = state.legal_moves()
        if len(legal) == 1:
            return legal[0]
        if state.all_tens_taken():  # nothing left to play for
            return self.policy.choose_card(state, player)
        forced, legal = self.apply_rules(state, player, legal)
        if forced is not None:
            return forced
        values = self.evaluate_cards(state, player, legal)
        self.last_eval = values
        return self.pick(state, values)

    #: Cards scoring within this much of the best (3% of a game) are treated
    #: as equally good. (In real play the search sees ~15k deals per move, so
    #: its noise is well inside this margin.)
    NEAR = 0.03

    def pick(self, state: GameState, values: dict[int, float], allowed=None) -> int:
        """Choose among searched moves (``values`` from ``evaluate_cards``).

        With ``conserve``: among the moves that are as good as the best, play
        the cheapest - don't put a 10 at risk, don't spend a trump, and keep
        high cards for later (e.g. J rather than A when the search can't
        separate them). In duplicate matches this cost no measurable strength.
        ``allowed`` (from ``apply_rules``) limits the choice when given.
        """
        if allowed is not None:
            values = {c: v for c, v in values.items() if c in allowed} or values
        best = max(values, key=values.get)
        if not self.conserve:
            fallback = self.policy.choose_card(state, state.turn)
            return fallback if values.get(fallback, -9.0) >= values[best] - 0.005 else best
        near = [c for c, v in values.items() if v >= values[best] - self.NEAR]
        trump = state.trump
        return min(near, key=lambda c: (c % 13 == TEN, c // 13 == trump, c % 13, -values[c]))

    def evaluate_trumps(self, state: GameState, player: int) -> list[float]:
        sums, n = self._run(_trump_job, lambda seed: (state.copy(), player,
                                                      self.trump_samples, self.trump_time, seed))
        return [s / n for s in sums]

    def choose_trump(self, state, player):
        values = self.evaluate_trumps(state, player)
        self.last_eval = values
        return max(range(4), key=lambda s: values[s])
