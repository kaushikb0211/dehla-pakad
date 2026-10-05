"""Run a single hand between four bots."""

from __future__ import annotations

import random

from .engine import PHASE_PLAY, GameState


def play_hand(bots, dealer: int = 0, seed=None, deck=None, stop_early: bool = True,
              variant: str = "classic") -> GameState:
    """Deal and play one hand. ``bots[i]`` controls seat i. With ``stop_early``
    the hand ends once all four 10s are captured."""
    if deck is None:
        deck = list(range(52))
        random.Random(seed).shuffle(deck)
    state = GameState.from_deck(deck, dealer, variant)
    state.declare_trump(bots[state.chooser].choose_trump(state, state.chooser))
    while state.phase == PHASE_PLAY and not (stop_early and state.all_tens_taken()):
        p = state.turn
        state.play(bots[p].choose_card(state, p))
    return state
