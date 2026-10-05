"""One player's game session: the human (seat 0) against three AIs.

Transport-agnostic: the desktop web server (``web/server.py``) and the
standalone phone version (Pyodide in the browser, ``web/static/worker.js``)
both drive the game through ``Session.handle``, so they share every rule,
AI change and fix.
"""

from __future__ import annotations

import random

from .cards import TENS_MASK, cards_of
from .engine import CLASSIC, PHASE_DONE, PHASE_PLAY, PHASE_TRUMP, VARIANTS, GameState, IllegalMove
from .mc import MCBot

HUMAN = 0
NAMES = ["You", "West", "North", "East"]


class Session:
    def __init__(self, executor=None, workers: int = 1, think_time: float = 4.0):
        self.executor = executor
        self.workers = workers if executor else 1
        self.rng = random.Random()
        self.think_time = think_time
        self.dealer = 3  # first hand: you are dealer's left, so you call trump
        self.state: GameState | None = None
        self.variant = CLASSIC
        self.tallies = {v: {"win": 0, "tie": 0, "loss": 0} for v in VARIANTS}
        self.hands_played = 0
        self._make_bots()

    def _make_bots(self):
        self.bots = {p: MCBot(time_limit=self.think_time, executor=self.executor, workers=self.workers,
                              seed=self.rng.randrange(2**31)) for p in (1, 2, 3)}
        self.advisor = MCBot(time_limit=min(self.think_time, 2.5), executor=self.executor,
                             workers=self.workers, seed=self.rng.randrange(2**31))

    # ---------------------------------------------------------------- requests
    def handle(self, name: str, body: dict | None = None) -> dict:
        """Run one request (``state``, ``new``, ``play`` ...). Raises
        IllegalMove for anything the rules don't allow."""
        body = body or {}
        if name == "state":
            return {"events": [], "state": self.view()}
        if name == "hint":
            return self.hint()
        if name == "settings":
            self.think_time = max(0.2, min(10.0, float(body["think_time"])))
            self._make_bots()
            return {"events": [], "state": self.view()}
        if name == "prefer_variant":  # restore saved rules without dealing (phone version)
            if body.get("variant") in VARIANTS and self.state is None:
                self.variant = body["variant"]
            return {"events": [], "state": self.view()}
        if name == "tallies":  # restore saved scores (phone version)
            for v in VARIANTS:
                if v in body:
                    self.tallies[v] = {k: int(body[v].get(k, 0)) for k in ("win", "tie", "loss")}
            return {"events": [], "state": self.view()}
        actions = {
            "new": self.new_hand,
            "reset": self.reset,
            "variant": lambda: self.set_variant(body["variant"]),
            "trump": lambda: self._human_trump(int(body["suit"])),
            "play": lambda: self._human_play(int(body["card"])),
            "ai_step": self.ai_step,
        }
        if name not in actions:
            raise IllegalMove(f"unknown request {name!r}")
        events = actions[name]()
        return {"events": events, "state": self.view()}

    def _human_trump(self, suit):
        s = self.state
        if s is None or s.phase != PHASE_TRUMP or s.chooser != HUMAN:
            raise IllegalMove("you are not declaring trump")
        return self.declare(HUMAN, suit)

    def _human_play(self, card):
        if self.state is None or self.state.phase != PHASE_PLAY:
            raise IllegalMove("no hand in progress")
        return self.play(HUMAN, card)

    # ---------------------------------------------------------------- actions
    def new_hand(self):
        if self.state is not None:
            self.dealer = (self.dealer + 1) & 3
        self.state = GameState.new_game(self.rng, self.dealer, self.variant)
        return [{"type": "deal", "round": 1}]

    def set_variant(self, variant):
        """Switch rules; takes effect with a freshly dealt hand."""
        if variant not in VARIANTS:
            raise IllegalMove(f"unknown variant {variant!r}")
        self.variant = variant
        return self.new_hand()

    def reset(self):
        """Clear the session tallies and start over with a fresh hand."""
        self.tallies = {v: {"win": 0, "tie": 0, "loss": 0} for v in VARIANTS}
        self.hands_played = 0
        self.dealer = 3
        self.state = None
        return self.new_hand()

    def declare(self, player, suit):
        s = self.state
        s.declare_trump(suit)
        return [{"type": "trump", "player": player, "suit": suit}, {"type": "deal", "round": 2}]

    def play(self, player, card):
        s = self.state
        if s.turn != player:
            raise IllegalMove("not your turn")
        winner = s.play(card)
        events = [{"type": "play", "player": player, "card": card}]
        if winner is not None:
            t = s.history[-1]
            events.append({"type": "trick", "winner": winner, "cards": [[p, c] for p, c in t["cards"]],
                           "collected": bool(t["collected"]),
                           "collected_tricks": t["collected_tricks"],
                           "tens": cards_of(t["collected"] & TENS_MASK),
                           "pot": cards_of(s.pot), "streak": s.streak})
            if s.all_tens_taken() and s.phase != PHASE_DONE:
                s.phase = PHASE_DONE
            if s.phase == PHASE_DONE:
                o = s.outcome(0)
                self.tallies[s.variant]["win" if o > 0 else "tie" if o == 0 else "loss"] += 1
                self.hands_played += 1
                events.append({"type": "end", "outcome": o})
        return events

    def ai_step(self):
        s = self.state
        if s is None:
            raise IllegalMove("no hand in progress")
        if s.phase == PHASE_TRUMP and s.chooser != HUMAN:
            p = s.chooser
            return self.declare(p, self.bots[p].choose_trump(s, p))
        if s.phase == PHASE_PLAY and s.turn != HUMAN:
            p = s.turn
            return self.play(p, self.bots[p].choose_card(s, p))
        raise IllegalMove("waiting for the human")

    def hint(self):
        s = self.state
        if s is not None and s.phase == PHASE_TRUMP and s.chooser == HUMAN:
            vals = self.advisor.evaluate_trumps(s, HUMAN)
            return {"kind": "trump", "values": {str(i): v for i, v in enumerate(vals)},
                    "best": max(range(4), key=lambda i: vals[i])}
        if s is not None and s.phase == PHASE_PLAY and s.turn == HUMAN:
            vals = self.advisor.evaluate_cards(s, HUMAN)
            if len(vals) == 1:
                (only,) = vals
                return {"kind": "card", "values": {str(only): 0.0}, "best": only, "tied": [only]}
            # Recommend exactly what the AIs would play: their play rules
            # (forced 10s, sure collects, no risky 10 leads, ...) first, then
            # the cheapest of the cards as good as the best. The numbers for
            # every card are still shown.
            forced, allowed = self.advisor.apply_rules(s, HUMAN, list(vals))
            top = max(vals[c] for c in allowed)
            tied = [c for c in allowed if vals[c] >= top - MCBot.NEAR]
            best = forced if forced is not None else self.advisor.pick(s, vals, allowed)
            return {"kind": "card", "values": {str(c): v for c, v in vals.items()},
                    "best": best, "tied": tied, "worlds": self.advisor._stats[2]}
        raise IllegalMove("nothing to advise on")

    # ---------------------------------------------------------------- view
    def view(self):
        s = self.state
        if s is None:
            return {"phase": "none", "tally": self.tallies[self.variant], "tallies": self.tallies,
                    "think_time": self.think_time, "variant": self.variant}
        legal = []
        if s.phase == PHASE_PLAY and s.turn == HUMAN:
            legal = s.legal_moves()
        last = s.history[-1] if s.history else None
        return {
            "phase": s.phase,
            "dealer": s.dealer,
            "chooser": s.chooser,
            "trump": s.trump,
            "turn": s.turn,
            "hands": [cards_of(h) for h in s.hands],
            "legal": legal,
            "trick": [[p, c] for p, c in s.trick],
            "tricks_won": s.tricks_won,
            "tens_won": s.tens_won,
            "tens_cards": [cards_of(m) for m in s.tens_cards],
            "tricks_played": s.tricks_played,
            "last_trick": {"cards": [[p, c] for p, c in last["cards"]], "winner": last["winner"]} if last else None,
            "outcome": s.outcome(0) if s.phase == PHASE_DONE else None,
            "tally": self.tallies[s.variant],
            "tallies": self.tallies,
            "think_time": self.think_time,
            "names": NAMES,
            "variant": s.variant,
            "pot": cards_of(s.pot),
            "streak": s.streak,
        }
