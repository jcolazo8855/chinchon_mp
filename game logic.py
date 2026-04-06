"""
game_logic.py — Pure Chinchón game logic, no framework dependencies.
All state is plain dicts/lists; fully serialisable to JSON.
"""

import random
from itertools import combinations

# ── Constants ──────────────────────────────────────────────────────────────────
SUITS  = ['Oros', 'Copas', 'Espadas', 'Bastos']
VALUES = [1, 2, 3, 4, 5, 6, 7, 10, 11, 12]   # no 8 or 9 in Spanish deck

SUIT_COLOR = {'Oros': '#8A6010', 'Copas': '#8A0000',
              'Espadas': '#1A3A80', 'Bastos': '#1E5A0E'}
SUIT_LIGHT = {'Oros': '#FFF8E0', 'Copas': '#FFE8E8',
              'Espadas': '#E8F0FF', 'Bastos': '#E8FFE8'}
SUIT_EMOJI = {'Oros': '🪙', 'Copas': '🍷', 'Espadas': '⚔️', 'Bastos': '🏑'}


def vlabel(v: int) -> str:
    return {1: 'A'}.get(v, str(v))

def vpoints(v: int) -> int:
    return {1:1, 2:2, 3:3, 4:4, 5:5, 6:6, 7:7, 10:10, 11:10, 12:10}[v]

def card_display(c: dict) -> dict:
    """Augment a card dict with display fields."""
    return {**c,
            'label': vlabel(c['v']),
            'color': SUIT_COLOR[c['s']],
            'light': SUIT_LIGHT[c['s']],
            'emoji': SUIT_EMOJI[c['s']]}

# ── Deck ───────────────────────────────────────────────────────────────────────
def make_deck() -> list[dict]:
    deck = [{'v': v, 's': s, 'id': si * 10 + vi}
            for si, s in enumerate(SUITS)
            for vi, v in enumerate(VALUES)]
    random.shuffle(deck)
    return deck

# ── Meld detection ─────────────────────────────────────────────────────────────
def _rank(v: int) -> int:
    return VALUES.index(v)

def is_group(cards: list) -> bool:
    return len(cards) >= 3 and len(set(c['v'] for c in cards)) == 1

def is_sequence(cards: list) -> bool:
    if len(cards) < 3:
        return False
    if len(set(c['s'] for c in cards)) != 1:
        return False
    rnks = sorted(_rank(c['v']) for c in cards)
    return all(rnks[i+1] == rnks[i] + 1 for i in range(len(rnks) - 1))

def is_meld(cards: list) -> bool:
    return bool(cards) and (is_group(cards) or is_sequence(cards))

def is_chinchon(hand: list) -> bool:
    return len(hand) == 7 and is_sequence(hand)

def find_win(hand: list) -> tuple[bool, bool, list]:
    """Returns (can_win, is_chinchon, melds)."""
    if len(hand) != 7:
        return False, False, []
    if is_chinchon(hand):
        return True, True, [hand[:]]
    if is_group(hand):
        return True, False, [hand[:]]
    for idx3 in combinations(range(7), 3):
        m1 = [hand[i] for i in idx3]
        m2 = [hand[i] for i in range(7) if i not in idx3]
        if is_meld(m1) and is_meld(m2):
            return True, False, [m1, m2]
    return False, False, []

def winning_discards(hand8: list) -> list[tuple[int, bool]]:
    """Return list of (idx, is_chinchon) winning discard positions."""
    if len(hand8) != 8:
        return []
    result = []
    for i in range(8):
        rem = [hand8[j] for j in range(8) if j != i]
        can, cc, _ = find_win(rem)
        if can:
            result.append((i, cc))
    return result

# ── Deadwood ───────────────────────────────────────────────────────────────────
def deadwood(hand: list) -> tuple[int, list]:
    """Return (min_penalty, unmatched_cards) for a 7-card hand."""
    best_pen = sum(vpoints(c['v']) for c in hand)
    best_rem = hand[:]

    for sz in range(3, 8):
        for idx in combinations(range(len(hand)), sz):
            sub = [hand[i] for i in idx]
            if not is_meld(sub):
                continue
            rem = [hand[i] for i in range(len(hand)) if i not in idx]
            pen = sum(vpoints(c['v']) for c in rem)
            if pen < best_pen:
                best_pen, best_rem = pen, rem

    for sz1 in range(3, 5):
        for idx1 in combinations(range(len(hand)), sz1):
            m1 = [hand[i] for i in idx1]
            if not is_meld(m1):
                continue
            rem1 = [i for i in range(len(hand)) if i not in idx1]
            for sz2 in range(3, len(rem1) + 1):
                for sub2 in combinations(range(len(rem1)), sz2):
                    m2 = [hand[rem1[j]] for j in sub2]
                    if not is_meld(m2):
                        continue
                    used = set(idx1) | {rem1[j] for j in sub2}
                    rem  = [hand[i] for i in range(len(hand)) if i not in used]
                    pen  = sum(vpoints(c['v']) for c in rem)
                    if pen < best_pen:
                        best_pen, best_rem = pen, rem

    return best_pen, best_rem

# ── Game state ─────────────────────────────────────────────────────────────────
def new_game(names: list[str]) -> dict:
    """Create a fresh game state for 2 players."""
    deck = make_deck()
    hands = [[deck.pop() for _ in range(7)],
             [deck.pop() for _ in range(7)]]
    discard = [deck.pop()]
    return {
        'names':       names,
        'scores':      [0, 0],
        'hands':       hands,
        'deck':        deck,
        'discard':     discard,
        'phase':       'draw',       # draw | discard | hand_over | match_over
        'turn':        0,            # 0 or 1
        'drawn_idx':   None,
        'win_idx':     [],           # [(idx, is_cc), ...]
        'message':     f"Hand begins — {names[0]}'s turn to draw.",
        'result':      None,         # None | 'p0_wins' | 'p1_wins' | 'p0_cc' | 'p1_cc' | 'declare'
        'melds':       [],
        'penalties':   [None, None],
        'unmatched':   [[], []],
        'hand_num':    1,
    }


def new_hand(state: dict) -> dict:
    """Start a new hand preserving scores and names."""
    new = new_game(state['names'])
    new['scores']   = state['scores']
    new['hand_num'] = state.get('hand_num', 1) + 1
    winner_of_last = state.get('_last_turn_winner')
    # The player who won the last hand goes first next hand
    if winner_of_last is not None:
        new['turn'] = winner_of_last
    new['message'] = f"Hand {new['hand_num']} — {new['names'][new['turn']]}'s turn to draw."
    return new


# ── Actions ────────────────────────────────────────────────────────────────────
def _reshuffle_if_needed(state: dict):
    """Reshuffle discard pile into deck if deck is empty."""
    if not state['deck']:
        if len(state['discard']) > 1:
            top = state['discard'].pop()
            random.shuffle(state['discard'])
            state['deck'].extend(state['discard'])
            state['discard'] = [top]


def action_draw_deck(state: dict, player: int) -> tuple[bool, str]:
    if state['phase'] != 'draw' or state['turn'] != player:
        return False, "Not your turn to draw."
    _reshuffle_if_needed(state)
    if not state['deck']:
        return False, "Deck is empty and cannot reshuffle."
    card = state['deck'].pop()
    state['hands'][player].append(card)
    state['drawn_idx'] = len(state['hands'][player]) - 1
    state['phase']     = 'discard'
    state['win_idx']   = winning_discards(state['hands'][player])
    names = state['names']
    state['message']   = f"{names[player]}: pick a card to discard."
    return True, "ok"


def action_draw_discard(state: dict, player: int) -> tuple[bool, str]:
    if state['phase'] != 'draw' or state['turn'] != player:
        return False, "Not your turn to draw."
    if not state['discard']:
        return False, "Discard pile is empty."
    card = state['discard'].pop()
    state['hands'][player].append(card)
    state['drawn_idx'] = len(state['hands'][player]) - 1
    state['phase']     = 'discard'
    state['win_idx']   = winning_discards(state['hands'][player])
    names = state['names']
    state['message']   = f"{names[player]}: pick a card to discard."
    return True, "ok"


def _apply_win(state: dict, player: int, hand7: list,
               cc: bool, melds: list) -> None:
    """Apply a winning hand: update scores and phase."""
    opp  = 1 - player
    pen, unmatched = deadwood(state['hands'][opp])
    state['hands'][player] = hand7
    state['melds']         = melds
    state['phase']         = 'hand_over'
    state['penalties']     = [None, None]
    state['unmatched']     = [[], []]

    if cc:
        result = f'p{player}_cc'
        state['penalties'][player] = 'cc'
        state['message'] = (f"🏅 ¡CHINCHÓN! {state['names'][player]} wins the game!")
    else:
        result = f'p{player}_wins'
        state['scores'][opp]       = state['scores'][opp] + pen
        state['scores'][player]    = max(0, state['scores'][player] - 10)
        state['penalties'][player] = -10
        state['penalties'][opp]    = pen
        state['unmatched'][opp]    = unmatched
        state['message'] = (
            f"✋ {state['names'][player]} wins! "
            f"{state['names'][opp]} pays +{pen} pts · "
            f"{state['names'][player]} gets −10 bonus."
        )

    state['result']           = result
    state['_last_turn_winner'] = player

    # Check match over (100+) — only relevant for normal wins
    if not cc:
        if state['scores'][0] >= 100 or state['scores'][1] >= 100:
            state['phase'] = 'match_over'
            loser  = 0 if state['scores'][0] >= 100 else 1
            winner = 1 - loser
            state['message'] = (
                f"🏆 {state['names'][winner]} wins the match! "
                f"{state['names'][loser]} reached 100 points."
            )


def action_discard(state: dict, player: int, idx: int) -> tuple[bool, str]:
    if state['phase'] != 'discard' or state['turn'] != player:
        return False, "Not your turn to discard."
    hand = state['hands'][player]
    if not (0 <= idx < len(hand)):
        return False, "Invalid card index."

    discarded         = hand[idx]
    remaining         = [hand[j] for j in range(len(hand)) if j != idx]
    state['discard'].append(discarded)
    can, cc, melds    = find_win(remaining)

    if can:
        _apply_win(state, player, remaining, cc, melds)
    else:
        state['hands'][player] = remaining
        state['drawn_idx']     = None
        state['win_idx']       = []
        state['phase']         = 'draw'
        state['turn']          = 1 - player
        names = state['names']
        state['message']       = f"{names[1-player]}'s turn — draw a card."

    return True, "ok"


def action_declare(state: dict, player: int) -> tuple[bool, str]:
    """Score the hand immediately, even if not a winning set."""
    if state['phase'] not in ('draw', 'discard') or state['turn'] != player:
        return False, "Not your turn."

    # If in discard phase (8 cards), pick best discard first
    hand = state['hands'][player]
    if len(hand) == 8:
        best_i = 0; best_dw = float('inf')
        for i in range(8):
            rem = [hand[j] for j in range(8) if j != i]
            can, cc, _ = find_win(rem)
            if can:
                best_i = i; best_dw = -1; break
            dw, _ = deadwood(rem)
            if dw < best_dw:
                best_dw, best_i = dw, i
        discarded = hand[best_i]
        hand7 = [hand[j] for j in range(8) if j != best_i]
        state['discard'].append(discarded)
    else:
        hand7 = list(hand)

    can, cc, melds = find_win(hand7)
    if can:
        _apply_win(state, player, hand7, cc, melds)
        return True, "ok"

    # Incomplete — both pay deadwood
    opp = 1 - player
    p_pen, p_unmatched = deadwood(hand7)
    c_pen, c_unmatched = deadwood(state['hands'][opp])
    state['scores'][player] += p_pen
    state['scores'][opp]    += c_pen
    state['hands'][player]   = hand7
    state['penalties']       = [None, None]
    state['penalties'][player] = p_pen
    state['penalties'][opp]    = c_pen
    state['unmatched']         = [[], []]
    state['unmatched'][player] = p_unmatched
    state['unmatched'][opp]    = c_unmatched
    state['melds']             = []
    state['phase']             = 'hand_over'
    state['result']            = 'declare'
    state['_last_turn_winner'] = player
    names = state['names']
    state['message'] = (
        f"🗒 {names[player]} declares. "
        f"{names[player]} +{p_pen} pts · {names[opp]} +{c_pen} pts."
    )

    if state['scores'][0] >= 100 or state['scores'][1] >= 100:
        state['phase'] = 'match_over'
        loser  = 0 if state['scores'][0] >= 100 else 1
        winner = 1 - loser
        state['message'] = (
            f"🏆 {state['names'][winner]} wins the match! "
            f"{state['names'][loser]} reached 100 points."
        )

    return True, "ok"


def action_move(state: dict, player: int, i: int, j: int) -> tuple[bool, str]:
    """Swap cards at positions i and j in player's hand."""
    if state['turn'] != player or state['phase'] not in ('draw', 'discard'):
        return False, "Not your turn."
    hand = list(state['hands'][player])
    n = len(hand)
    if not (0 <= i < n and 0 <= j < n):
        return False, "Invalid indices."
    # Track drawn card by id
    drawn_id = (hand[state['drawn_idx']]['id']
                if state['drawn_idx'] is not None else None)
    hand[i], hand[j] = hand[j], hand[i]
    state['hands'][player] = hand
    if drawn_id is not None:
        state['drawn_idx'] = next(
            (k for k, c in enumerate(hand) if c['id'] == drawn_id), None)
    if state['phase'] == 'discard':
        state['win_idx'] = winning_discards(hand)
    return True, "ok"


# ── View helpers (what each player is allowed to see) ─────────────────────────
def player_view(state: dict, player: int) -> dict:
    """Build the JSON payload for one player's browser."""
    opp     = 1 - player
    my_turn = (state['turn'] == player
               and state['phase'] in ('draw', 'discard'))
    # Annotate my hand with display fields + win/new markers
    win_set = {i for i, _ in state['win_idx']} if state['turn'] == player else set()
    win_cc  = {i for i, cc in state['win_idx'] if cc} if state['turn'] == player else set()

    my_hand = []
    for k, c in enumerate(state['hands'][player]):
        cd = card_display(c)
        cd['is_new'] = (k == state['drawn_idx']) and (state['phase'] == 'discard') and state['turn'] == player
        cd['is_cc']  = k in win_cc
        cd['is_win'] = k in win_set
        my_hand.append(cd)

    # Opponent's hand: face-up only at hand/match over, else face-down stubs
    phase = state['phase']
    reveal_opp = phase in ('hand_over', 'match_over')
    if reveal_opp:
        opp_hand = [card_display(c) for c in state['hands'][opp]]
    else:
        opp_hand = [{'facedown': True} for _ in state['hands'][opp]]

    # Melds (for display after winning)
    melds_out = []
    for meld in state.get('melds', []):
        kind = 'Chinchón!' if is_chinchon(meld) else \
               'Sequence'  if is_sequence(meld) else 'Group'
        melds_out.append({'kind': kind, 'cards': [card_display(c) for c in meld]})

    # Unmatched cards (deadwood reveal)
    unmatched_mine = [card_display(c) for c in state['unmatched'][player]]
    unmatched_opp  = [card_display(c) for c in state['unmatched'][opp]]

    discard_top = card_display(state['discard'][-1]) if state['discard'] else None

    return {
        'phase':           phase,
        'my_turn':         my_turn,
        'my_idx':          player,
        'opp_idx':         opp,
        'names':           state['names'],
        'scores':          state['scores'],
        'my_hand':         my_hand,
        'opp_hand':        opp_hand,
        'reveal_opp':      reveal_opp,
        'discard_top':     discard_top,
        'deck_size':       len(state['deck']),
        'message':         state['message'],
        'result':          state.get('result'),
        'melds':           melds_out,
        'penalties':       state.get('penalties', [None, None]),
        'unmatched_mine':  unmatched_mine,
        'unmatched_opp':   unmatched_opp,
        'hand_num':        state.get('hand_num', 1),
        'can_declare':     state['turn'] == player and phase in ('draw', 'discard'),
        'win_idx':         list(state['win_idx']) if state['turn'] == player else [],
    }
