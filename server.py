"""
server.py — Chinchón Multiplayer  (single-file, self-contained)
Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""
import asyncio, base64, json, logging, random, string, time, math
from itertools import combinations
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Inline game logic ────────────────────────────────────────────────────────
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

# ── Embedded frontend (base64) ────────────────────────────────────────────────
_HTML_B64 = (
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVU"
    "Ri04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwg"
    "aW5pdGlhbC1zY2FsZT0xLjAiPgo8dGl0bGU+Q2hpbmNow7NuIOKAlCBNdWx0aXBsYXllcjwvdGl0"
    "bGU+CjxzdHlsZT4KLyog4pSA4pSAIFJlc2V0ICYgYmFzZSDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KKiwgKjo6YmVmb3JlLCAq"
    "OjphZnRlciB7IGJveC1zaXppbmc6IGJvcmRlci1ib3g7IG1hcmdpbjogMDsgcGFkZGluZzogMDsg"
    "fQpodG1sLCBib2R5IHsKICBoZWlnaHQ6IDEwMCU7IG1pbi1oZWlnaHQ6IDEwMHZoOwogIGZvbnQt"
    "ZmFtaWx5OiAnU2Vnb2UgVUknLCBzeXN0ZW0tdWksIHNhbnMtc2VyaWY7CiAgYmFja2dyb3VuZDog"
    "cmFkaWFsLWdyYWRpZW50KGVsbGlwc2UgYXQgNTAlIDIwJSwgIzFkNmIzNSAwJSwgIzE0NTIyOCA1"
    "NSUsICMwYzNhMWMgMTAwJSk7CiAgY29sb3I6ICNmZmY7CiAgb3ZlcmZsb3cteDogaGlkZGVuOwp9"
    "CgovKiDilIDilIAgTG9iYnkgLyBKb2luIHNjcmVlbiDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIAgKi8KI2xvYmJ5IHsKICBkaXNwbGF5OiBmbGV4OyBmbGV4LWRpcmVjdGlv"
    "bjogY29sdW1uOyBhbGlnbi1pdGVtczogY2VudGVyOwogIGp1c3RpZnktY29udGVudDogY2VudGVy"
    "OyBtaW4taGVpZ2h0OiAxMDB2aDsgZ2FwOiAxOHB4OyBwYWRkaW5nOiAyNHB4Owp9CiNsb2JieSBo"
    "MSB7IGZvbnQtc2l6ZTogM3JlbTsgZm9udC13ZWlnaHQ6IDkwMDsgY29sb3I6ICNmYmJmMjQ7IHRl"
    "eHQtc2hhZG93OiAwIDJweCAxMnB4ICMwMDA2OyB9CiNsb2JieSBwICB7IGNvbG9yOiByZ2JhKDI1"
    "NSwyNTUsMjU1LC42KTsgZm9udC1zaXplOiAuOTVyZW07IHRleHQtYWxpZ246IGNlbnRlcjsgfQou"
    "bG9iYnktY2FyZCB7CiAgYmFja2dyb3VuZDogcmdiYSgwLDAsMCwuNCk7IGJvcmRlcjogMXB4IHNv"
    "bGlkIHJnYmEoMjU1LDI1NSwyNTUsLjEyKTsKICBib3JkZXItcmFkaXVzOiAxNHB4OyBwYWRkaW5n"
    "OiAyNnB4IDMycHg7IHdpZHRoOiBtaW4oMzYwcHgsIDkydncpOwogIGRpc3BsYXk6IGZsZXg7IGZs"
    "ZXgtZGlyZWN0aW9uOiBjb2x1bW47IGdhcDogMTJweDsKfQoubG9iYnktY2FyZCBoMiB7IGZvbnQt"
    "c2l6ZTogMS4xcmVtOyBjb2xvcjogIzg2ZWZhYzsgfQppbnB1dFt0eXBlPXRleHRdIHsKICB3aWR0"
    "aDogMTAwJTsgcGFkZGluZzogMTBweCAxM3B4OyBib3JkZXItcmFkaXVzOiA4cHg7IGJvcmRlcjog"
    "MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsLjIpOwogIGJhY2tncm91bmQ6IHJnYmEoMjU1LDI1"
    "NSwyNTUsLjA4KTsgY29sb3I6ICNmZmY7IGZvbnQtc2l6ZTogMXJlbTsgb3V0bGluZTogbm9uZTsK"
    "fQppbnB1dFt0eXBlPXRleHRdOjpwbGFjZWhvbGRlciB7IGNvbG9yOiByZ2JhKDI1NSwyNTUsMjU1"
    "LC4zNSk7IH0KaW5wdXRbdHlwZT10ZXh0XTpmb2N1cyB7IGJvcmRlci1jb2xvcjogIzYwYTVmYTsg"
    "fQouYnRuIHsKICBkaXNwbGF5OiBpbmxpbmUtZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVz"
    "dGlmeS1jb250ZW50OiBjZW50ZXI7IGdhcDogNnB4OwogIHBhZGRpbmc6IDEwcHggMThweDsgYm9y"
    "ZGVyLXJhZGl1czogOHB4OyBib3JkZXI6IG5vbmU7IGN1cnNvcjogcG9pbnRlcjsKICBmb250LXNp"
    "emU6IC45NXJlbTsgZm9udC13ZWlnaHQ6IDcwMDsgdHJhbnNpdGlvbjogYWxsIC4xNXM7Cn0KLmJ0"
    "bjpob3ZlciAgeyBmaWx0ZXI6IGJyaWdodG5lc3MoMS4xKTsgdHJhbnNmb3JtOiB0cmFuc2xhdGVZ"
    "KC0xcHgpOyB9Ci5idG46YWN0aXZlIHsgdHJhbnNmb3JtOiBzY2FsZSguOTgpOyB9Ci5idG4tZ3Jl"
    "ZW4gIHsgYmFja2dyb3VuZDogIzE2YTM0YTsgY29sb3I6ICNmZmY7IHdpZHRoOiAxMDAlOyB9Ci5i"
    "dG4tYmx1ZSAgIHsgYmFja2dyb3VuZDogIzI1NjNlYjsgY29sb3I6ICNmZmY7IHdpZHRoOiAxMDAl"
    "OyB9Ci5idG4tb3JhbmdlIHsgYmFja2dyb3VuZDogI2VhNTgwYzsgY29sb3I6ICNmZmY7IH0KLmJ0"
    "bi1yZWQgICAgeyBiYWNrZ3JvdW5kOiAjZGMyNjI2OyBjb2xvcjogI2ZmZjsgfQouYnRuLWdyYXkg"
    "ICB7IGJhY2tncm91bmQ6IHJnYmEoMjU1LDI1NSwyNTUsLjE1KTsgY29sb3I6ICNmZmY7IH0KLmJ0"
    "bi1nb2xkICAgeyBiYWNrZ3JvdW5kOiAjZDk3NzA2OyBjb2xvcjogI2ZmZjsgfQouYnRuLXNtIHsg"
    "cGFkZGluZzogNXB4IDEycHg7IGZvbnQtc2l6ZTogLjgycmVtOyB9Ci5idG46ZGlzYWJsZWQgeyBv"
    "cGFjaXR5OiAuNDsgY3Vyc29yOiBub3QtYWxsb3dlZDsgdHJhbnNmb3JtOiBub25lOyB9CgovKiDi"
    "lIDilIAgR2FtZSBib2FyZCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KI2dhbWUgeyBkaXNwbGF5OiBub25lOyBmbGV4"
    "LWRpcmVjdGlvbjogY29sdW1uOyBtaW4taGVpZ2h0OiAxMDB2aDsgfQoKLyogSGVhZGVyIGJhciAq"
    "LwojaGVhZGVyIHsKICBiYWNrZ3JvdW5kOiByZ2JhKDAsMCwwLC40NSk7IGJvcmRlci1ib3R0b206"
    "IDFweCBzb2xpZCByZ2JhKDI1NSwyNTUsMjU1LC4xKTsKICBwYWRkaW5nOiA4cHggMTZweDsgZGlz"
    "cGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsgZ2FwOiAxMnB4OyBmbGV4LXdyYXA6IHdy"
    "YXA7Cn0KI2hlYWRlciAucm9vbS1jb2RlIHsKICBmb250LXNpemU6IC44cmVtOyBjb2xvcjogcmdi"
    "YSgyNTUsMjU1LDI1NSwuNSk7IGxldHRlci1zcGFjaW5nOiAycHg7IHRleHQtdHJhbnNmb3JtOiB1"
    "cHBlcmNhc2U7Cn0KI2hlYWRlciAucm9vbS1jb2RlIHNwYW4geyBjb2xvcjogI2ZiYmYyNDsgZm9u"
    "dC13ZWlnaHQ6IDgwMDsgbGV0dGVyLXNwYWNpbmc6IDNweDsgfQouc2NvcmUtYm94IHsKICBiYWNr"
    "Z3JvdW5kOiByZ2JhKDAsMCwwLC4zKTsgYm9yZGVyOiAxcHggc29saWQgcmdiYSgyNTUsMjU1LDI1"
    "NSwuMSk7CiAgYm9yZGVyLXJhZGl1czogOHB4OyBwYWRkaW5nOiA1cHggMTRweDsgdGV4dC1hbGln"
    "bjogY2VudGVyOyBtaW4td2lkdGg6IDgwcHg7Cn0KLnNjb3JlLWJveCAuc25hbWUgeyBmb250LXNp"
    "emU6IC42NXJlbTsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjQpOyB0ZXh0LXRyYW5zZm9ybTog"
    "dXBwZXJjYXNlOyBsZXR0ZXItc3BhY2luZzogMXB4OyB9Ci5zY29yZS1ib3ggLnN2YWwgIHsgZm9u"
    "dC1zaXplOiAxLjI1cmVtOyBmb250LXdlaWdodDogODAwOyB9Ci5zLWdyZWVuICB7IGNvbG9yOiAj"
    "ODZlZmFjOyB9Ci5zLW9yYW5nZSB7IGNvbG9yOiAjZmI5MjNjOyB9Ci5zLXJlZCAgICB7IGNvbG9y"
    "OiAjZjg3MTcxOyB9CiNoZWFkZXIgLnNwYWNlciB7IGZsZXg6IDE7IH0KI3R1cm4tYmFubmVyIHsK"
    "ICBwYWRkaW5nOiA1cHggMTRweDsgYm9yZGVyLXJhZGl1czogOHB4OyBmb250LXNpemU6IC44NXJl"
    "bTsgZm9udC13ZWlnaHQ6IDcwMDsKICB0cmFuc2l0aW9uOiBhbGwgLjNzOwp9Ci50dXJuLW1pbmUg"
    "eyBiYWNrZ3JvdW5kOiAjMTZhMzRhOyBjb2xvcjogI2ZmZjsgfQoudHVybi1vcHAgIHsgYmFja2dy"
    "b3VuZDogcmdiYSgyNTUsMjU1LDI1NSwuMTIpOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwuNik7"
    "IH0KCi8qIEJvYXJkIGxheW91dCAqLwojYm9hcmQgewogIGZsZXg6IDE7IGRpc3BsYXk6IGdyaWQ7"
    "CiAgZ3JpZC10ZW1wbGF0ZS1yb3dzOiBhdXRvIGF1dG8gMWZyIGF1dG8gYXV0bzsKICBnYXA6IDEw"
    "cHg7IHBhZGRpbmc6IDEycHggMTRweDsgbWF4LXdpZHRoOiAxMTAwcHg7IG1hcmdpbjogMCBhdXRv"
    "OyB3aWR0aDogMTAwJTsKfQoKLyogU2VjdGlvbiBsYWJlbHMgKi8KLnNlYy1sYmwgewogIGZvbnQt"
    "c2l6ZTogLjY1cmVtOyBmb250LXdlaWdodDogNzAwOyBsZXR0ZXItc3BhY2luZzogMnB4OwogIGNv"
    "bG9yOiByZ2JhKDI1NSwyNTUsMjU1LC40KTsgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsgbWFy"
    "Z2luLWJvdHRvbTogNXB4Owp9CgovKiBPcHBvbmVudCBhcmVhICovCiNvcHAtYXJlYSB7IH0KI29w"
    "cC1uYW1lLXJvdyB7CiAgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsgZ2FwOiAx"
    "MHB4OyBtYXJnaW4tYm90dG9tOiA2cHg7Cn0KI29wcC1oYW5kIHsgZGlzcGxheTogZmxleDsgZ2Fw"
    "OiA1cHg7IGZsZXgtd3JhcDogbm93cmFwOyB9CgovKiBUYWJsZSBhcmVhICovCiN0YWJsZS1hcmVh"
    "IHsKICBkaXNwbGF5OiBmbGV4OyBnYXA6IDE0cHg7IGFsaWduLWl0ZW1zOiBmbGV4LXN0YXJ0Owog"
    "IGJhY2tncm91bmQ6IHJnYmEoMCwwLDAsLjE1KTsgYm9yZGVyLXJhZGl1czogMTBweDsgcGFkZGlu"
    "ZzogMTBweCAxNHB4Owp9Ci5waWxlIHsgZGlzcGxheTogZmxleDsgZmxleC1kaXJlY3Rpb246IGNv"
    "bHVtbjsgYWxpZ24taXRlbXM6IGNlbnRlcjsgZ2FwOiA2cHg7IH0KLnBpbGUtbGFiZWwgeyBmb250"
    "LXNpemU6IC43cmVtOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwuNCk7IH0KCi8qIE1lc3NhZ2Ug"
    "Ki8KI21zZy1ib3ggewogIGJhY2tncm91bmQ6IHJnYmEoMCwwLDAsLjMpOyBib3JkZXI6IDFweCBz"
    "b2xpZCByZ2JhKDI1NSwyNTUsMjU1LC4xMik7CiAgYm9yZGVyLXJhZGl1czogOHB4OyBwYWRkaW5n"
    "OiA4cHggMTRweDsgZm9udC1zaXplOiAuODVyZW07IHRleHQtYWxpZ246IGNlbnRlcjsKICBjb2xv"
    "cjogcmdiYSgyNTUsMjU1LDI1NSwuOCk7IG1pbi1oZWlnaHQ6IDM4cHg7IGZsZXg6IDE7Cn0KLm1z"
    "Zy13aW4gIHsgYm9yZGVyLWNvbG9yOiAjODZlZmFjICFpbXBvcnRhbnQ7IGNvbG9yOiAjODZlZmFj"
    "ICFpbXBvcnRhbnQ7IH0KLm1zZy1sb3NlIHsgYm9yZGVyLWNvbG9yOiAjZmNhNWE1ICFpbXBvcnRh"
    "bnQ7IGNvbG9yOiAjZmNhNWE1ICFpbXBvcnRhbnQ7IH0KLm1zZy1jYyAgIHsgYm9yZGVyLWNvbG9y"
    "OiAjZmJiZjI0ICFpbXBvcnRhbnQ7IGNvbG9yOiAjZmJiZjI0ICFpbXBvcnRhbnQ7IH0KCi8qIFBs"
    "YXllciBhcmVhICovCiNteS1hcmVhIHsgfQojbXktaGFuZC1yb3cgeyBkaXNwbGF5OiBmbGV4OyBn"
    "YXA6IDVweDsgZmxleC13cmFwOiBub3dyYXA7IG92ZXJmbG93LXg6IGF1dG87IHBhZGRpbmctYm90"
    "dG9tOiAzcHg7IH0KI3N3YXAtcm93ICAgICB7IGRpc3BsYXk6IGZsZXg7IGdhcDogNXB4OyBtYXJn"
    "aW4tdG9wOiA1cHg7IH0KI2Rpc2NhcmQtcm93ICB7IGRpc3BsYXk6IGZsZXg7IGdhcDogNXB4OyBt"
    "YXJnaW4tdG9wOiAzcHg7IH0KCi8qIEFjdGlvbiBiYXIgKi8KI2FjdGlvbi1iYXIgewogIGRpc3Bs"
    "YXk6IGZsZXg7IGdhcDogOHB4OyBmbGV4LXdyYXA6IHdyYXA7CiAgYmFja2dyb3VuZDogcmdiYSgw"
    "LDAsMCwuMjUpOyBib3JkZXItcmFkaXVzOiAxMHB4OyBwYWRkaW5nOiAxMHB4IDEycHg7Cn0KCi8q"
    "IFJlc3VsdCBvdmVybGF5ICovCiNyZXN1bHQtb3ZlcmxheSB7CiAgZGlzcGxheTogbm9uZTsgcG9z"
    "aXRpb246IGZpeGVkOyBpbnNldDogMDsgYmFja2dyb3VuZDogcmdiYSgwLDAsMCwuNyk7CiAgei1p"
    "bmRleDogMTAwOyBhbGlnbi1pdGVtczogY2VudGVyOyBqdXN0aWZ5LWNvbnRlbnQ6IGNlbnRlcjsg"
    "cGFkZGluZzogMjBweDsKfQojcmVzdWx0LWNhcmQgewogIGJhY2tncm91bmQ6ICMwZjJkMWE7IGJv"
    "cmRlcjogMnB4IHNvbGlkICNmYmJmMjQ7IGJvcmRlci1yYWRpdXM6IDE2cHg7CiAgcGFkZGluZzog"
    "MzBweCAzNnB4OyBtYXgtd2lkdGg6IDU2MHB4OyB3aWR0aDogMTAwJTsgdGV4dC1hbGlnbjogY2Vu"
    "dGVyOwp9CiNyZXN1bHQtY2FyZCBoMiB7IGZvbnQtc2l6ZTogMS41cmVtOyBmb250LXdlaWdodDog"
    "ODAwOyBtYXJnaW4tYm90dG9tOiAxMHB4OyB9CgovKiDilIDilIAgQ2FyZCBTVkcg4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSAICovCi5jYXJkLXdyYXAgeyBwb3NpdGlvbjogcmVsYXRpdmU7IGRpc3BsYXk6IGlu"
    "bGluZS1ibG9jazsgY3Vyc29yOiBkZWZhdWx0OyB9Ci5jYXJkLXN2Zy1vdXRlciB7CiAgd2lkdGg6"
    "IDcycHg7IGhlaWdodDogMTA4cHg7IGJvcmRlci1yYWRpdXM6IDdweDsgYm9yZGVyOiAxLjVweCBz"
    "b2xpZCAjYjhiOGI4OwogIGJhY2tncm91bmQ6ICNGRkZFRjA7IGJveC1zaGFkb3c6IDJweCA1cHgg"
    "MTBweCByZ2JhKDAsMCwwLC4zNSk7CiAgcG9zaXRpb246IHJlbGF0aXZlOyBvdmVyZmxvdzogaGlk"
    "ZGVuOyBkaXNwbGF5OiBpbmxpbmUtYmxvY2s7CiAgZmxleC1zaHJpbms6IDA7IHRyYW5zaXRpb246"
    "IHRyYW5zZm9ybSAuMTJzOwp9Ci5jYXJkLXN2Zy1vdXRlci5nbG93LW5ldyAgIHsgYm9yZGVyOiAz"
    "cHggc29saWQgIzYwYTVmYTsgYm94LXNoYWRvdzogMCAwIDE0cHggcmdiYSg5NiwxNjUsMjUwLC42"
    "NSksIDJweCA1cHggMTBweCByZ2JhKDAsMCwwLC40KTsgfQouY2FyZC1zdmctb3V0ZXIuZ2xvdy1j"
    "YyAgICB7IGJvcmRlcjogM3B4IHNvbGlkICNmYmJmMjQ7IGJveC1zaGFkb3c6IDAgMCAxOHB4ICNm"
    "YmJmMjQ4MCwgMnB4IDVweCAxMHB4IHJnYmEoMCwwLDAsLjQpOyB9Ci5jYXJkLXN2Zy1vdXRlci5n"
    "bG93LXdpbiAgIHsgYm9yZGVyOiAzcHggc29saWQgIzIyYzU1ZTsgYm94LXNoYWRvdzogMCAwIDEy"
    "cHggIzIyYzU1ZTY2LCAycHggNXB4IDEwcHggcmdiYSgwLDAsMCwuNCk7IH0KLmNhcmQtc3ZnLW91"
    "dGVyLmdsb3ctbWVsZCAgeyBib3JkZXI6IDNweCBzb2xpZCAjZmJiZjI0OyBib3gtc2hhZG93OiAw"
    "IDAgMTBweCAjZmJiZjI0NjA7IH0KLmNhcmQtc3ZnLW91dGVyOmhvdmVyLmNsaWNrYWJsZSB7IHRy"
    "YW5zZm9ybTogdHJhbnNsYXRlWSgtNnB4KTsgY3Vyc29yOiBwb2ludGVyOyB9Ci5jYXJkLWJhY2sg"
    "ewogIHdpZHRoOiA3MnB4OyBoZWlnaHQ6IDEwOHB4OyBib3JkZXItcmFkaXVzOiA3cHg7CiAgYmFj"
    "a2dyb3VuZDogbGluZWFyLWdyYWRpZW50KDE2MGRlZywjMWU0MGFmLCMyNTYzZWIgNjAlLCMxZTNh"
    "OGEpOwogIGJvcmRlcjogMnB4IHNvbGlkICM2MGE1ZmE7IGJveC1zaGFkb3c6IDJweCA1cHggMTBw"
    "eCByZ2JhKDAsMCwwLC41KTsKICBkaXNwbGF5OiBpbmxpbmUtZmxleDsgYWxpZ24taXRlbXM6IGNl"
    "bnRlcjsganVzdGlmeS1jb250ZW50OiBjZW50ZXI7CiAgZmxleC1zaHJpbms6IDA7IGZvbnQtc2l6"
    "ZTogMS42cmVtOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwuMik7Cn0KLmNhcmQtY29ybmVyIHsK"
    "ICBwb3NpdGlvbjogYWJzb2x1dGU7IGZvbnQtc2l6ZTogMTNweDsgZm9udC13ZWlnaHQ6IDkwMDsg"
    "bGluZS1oZWlnaHQ6IDE7CiAgdGV4dC1zaGFkb3c6IDAgMCA0cHggI2ZmZjgsIDAgMXB4IDJweCAj"
    "ZmZmOwp9Ci5jYXJkLWNvcm5lci50bCB7IHRvcDogM3B4OyBsZWZ0OiA0cHg7IH0KLmNhcmQtY29y"
    "bmVyLmJyIHsgYm90dG9tOiAzcHg7IHJpZ2h0OiA0cHg7IHRyYW5zZm9ybTogcm90YXRlKDE4MGRl"
    "Zyk7IH0KCi8qIFN3YXAgLyBkaXNjYXJkIGJ1dHRvbnMgYmVsb3cgY2FyZHMgKi8KLmNhcmQtYnRu"
    "LWNvbCB7IHdpZHRoOiA3MnB4OyBkaXNwbGF5OiBmbGV4OyBmbGV4LWRpcmVjdGlvbjogY29sdW1u"
    "OyBnYXA6IDJweDsgfQouY2FyZC1idG4tY29sIC5zd2FwLXBhaXIgeyBkaXNwbGF5OiBmbGV4OyBn"
    "YXA6IDJweDsgfQoubWluaS1idG4gewogIGZsZXg6IDE7IHBhZGRpbmc6IDNweCAwOyBib3JkZXIt"
    "cmFkaXVzOiA1cHg7IGJvcmRlcjogbm9uZTsgY3Vyc29yOiBwb2ludGVyOwogIGZvbnQtc2l6ZTog"
    "Ljc1cmVtOyBmb250LXdlaWdodDogNzAwOwogIGJhY2tncm91bmQ6IHJnYmEoMjU1LDI1NSwyNTUs"
    "LjEyKTsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjcpOwogIHRyYW5zaXRpb246IGJhY2tncm91"
    "bmQgLjEyczsKfQoubWluaS1idG46aG92ZXI6bm90KDpkaXNhYmxlZCkgeyBiYWNrZ3JvdW5kOiBy"
    "Z2JhKDI1NSwyNTUsMjU1LC4yNSk7IH0KLm1pbmktYnRuOmRpc2FibGVkIHsgb3BhY2l0eTogLjI1"
    "OyBjdXJzb3I6IGRlZmF1bHQ7IH0KLmRpc2MtYnRuIHsKICB3aWR0aDogNzJweDsgcGFkZGluZzog"
    "NHB4IDA7IGJvcmRlci1yYWRpdXM6IDVweDsgYm9yZGVyOiBub25lOyBjdXJzb3I6IHBvaW50ZXI7"
    "CiAgZm9udC1zaXplOiAuNzVyZW07IGZvbnQtd2VpZ2h0OiA3MDA7IHRyYW5zaXRpb246IGFsbCAu"
    "MTJzOwogIGJhY2tncm91bmQ6IHJnYmEoMCwwLDAsLjM1KTsgY29sb3I6IHJnYmEoMjU1LDI1NSwy"
    "NTUsLjcpOwp9Ci5kaXNjLWJ0bi5kLXN0b3AgICAgeyBiYWNrZ3JvdW5kOiAjMTZhMzRhOyBjb2xv"
    "cjogI2ZmZjsgfQouZGlzYy1idG4uZC1jaGluY2hvbiB7IGJhY2tncm91bmQ6ICNkOTc3MDY7IGNv"
    "bG9yOiAjZmZmOyBmb250LXNpemU6IC43cmVtOyB9Ci5kaXNjLWJ0bjpob3Zlcjpub3QoOmRpc2Fi"
    "bGVkKSB7IGZpbHRlcjogYnJpZ2h0bmVzcygxLjE1KTsgfQouZGlzYy1idG46ZGlzYWJsZWQgeyBv"
    "cGFjaXR5OiAuMzsgY3Vyc29yOiBkZWZhdWx0OyB9CgovKiBNZWxkIGRpc3BsYXkgKi8KLm1lbGQt"
    "YmxvY2sgeyBtYXJnaW4tdG9wOiA4cHg7IHRleHQtYWxpZ246IGxlZnQ7IH0KLm1lbGQtbGFiZWwg"
    "eyBmb250LXNpemU6IC43cmVtOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwuNDUpOyBtYXJnaW4t"
    "Ym90dG9tOiA0cHg7IH0KLm1lbGQtcm93IHsgZGlzcGxheTogZmxleDsgZ2FwOiA0cHg7IGZsZXgt"
    "d3JhcDogd3JhcDsgbWFyZ2luLWJvdHRvbTogNnB4OyB9CgovKiBQZW5hbHR5IHJvdyAqLwoucGVu"
    "YWx0eS1yb3cgeyBkaXNwbGF5OiBmbGV4OyBnYXA6IDIwcHg7IGp1c3RpZnktY29udGVudDogY2Vu"
    "dGVyOyBtYXJnaW46IDEwcHggMDsgfQoucGVuLWJveCB7IHRleHQtYWxpZ246IGNlbnRlcjsgfQou"
    "cGVuLW5hbWUgeyBmb250LXNpemU6IC43cmVtOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwuNCk7"
    "IHRleHQtdHJhbnNmb3JtOiB1cHBlcmNhc2U7IGxldHRlci1zcGFjaW5nOiAxcHg7IH0KLnBlbi12"
    "YWwgIHsgZm9udC1zaXplOiAxLjJyZW07IGZvbnQtd2VpZ2h0OiA4MDA7IH0KCi8qIFdhaXRpbmcg"
    "c3Bpbm5lciAqLwoud2FpdGluZyB7IHRleHQtYWxpZ246IGNlbnRlcjsgcGFkZGluZzogMjBweDsg"
    "Y29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjUpOyB9Ci5kb3Qtc3BpbiB7IGRpc3BsYXk6IGlubGlu"
    "ZS1ibG9jazsgYW5pbWF0aW9uOiBzcGluIDFzIGxpbmVhciBpbmZpbml0ZTsgfQpAa2V5ZnJhbWVz"
    "IHNwaW4geyB0byB7IHRyYW5zZm9ybTogcm90YXRlKDM2MGRlZyk7IH0gfQoKLyogTGVnZW5kICov"
    "CiNsZWdlbmQgewogIHRleHQtYWxpZ246IGNlbnRlcjsgZm9udC1zaXplOiAuNjVyZW07IGNvbG9y"
    "OiByZ2JhKDI1NSwyNTUsMjU1LC4yNSk7CiAgcGFkZGluZzogNnB4IDEwcHggMTBweDsKfQoKLyog"
    "RGlzY29ubmVjdGlvbiBiYW5uZXIgKi8KI2RjLWJhbm5lciB7CiAgZGlzcGxheTogbm9uZTsgcG9z"
    "aXRpb246IGZpeGVkOyB0b3A6IDA7IGxlZnQ6IDA7IHJpZ2h0OiAwOwogIGJhY2tncm91bmQ6ICM3"
    "ZjFkMWQ7IGNvbG9yOiAjZmNhNWE1OyB0ZXh0LWFsaWduOiBjZW50ZXI7CiAgcGFkZGluZzogOHB4"
    "IDE0cHg7IGZvbnQtc2l6ZTogLjg1cmVtOyB6LWluZGV4OiAyMDA7Cn0KPC9zdHlsZT4KPC9oZWFk"
    "Pgo8Ym9keT4KCjwhLS0g4pWQ4pWQIExPQkJZIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAt"
    "LT4KPGRpdiBpZD0ibG9iYnkiPgogIDxoMT7wn4OPIENoaW5jaMOzbjwvaDE+CiAgPHA+QmFyYWph"
    "IEVzcGHDsW9sYSDCtyA0MCBjYXJkcyDCtyAyIHBsYXllcnM8L3A+CgogIDxkaXYgY2xhc3M9Imxv"
    "YmJ5LWNhcmQiPgogICAgPGgyPvCfkaQgWW91ciBuYW1lPC9oMj4KICAgIDxpbnB1dCB0eXBlPSJ0"
    "ZXh0IiBpZD0iaW5wLW5hbWUiIHBsYWNlaG9sZGVyPSJFbnRlciB5b3VyIG5hbWXigKYiIG1heGxl"
    "bmd0aD0iMjAiPgogIDwvZGl2PgoKICA8ZGl2IGNsYXNzPSJsb2JieS1jYXJkIj4KICAgIDxoMj7w"
    "n4aVIENyZWF0ZSBhIHJvb208L2gyPgogICAgPHA+U2hhcmUgdGhlIDQtbGV0dGVyIGNvZGUgd2l0"
    "aCB5b3VyIG9wcG9uZW50LjwvcD4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tZ3JlZW4iIG9u"
    "Y2xpY2s9ImNyZWF0ZVJvb20oKSI+Q3JlYXRlIFJvb208L2J1dHRvbj4KICA8L2Rpdj4KCiAgPGRp"
    "diBjbGFzcz0ibG9iYnktY2FyZCI+CiAgICA8aDI+8J+UlyBKb2luIGEgcm9vbTwvaDI+CiAgICA8"
    "aW5wdXQgdHlwZT0idGV4dCIgaWQ9ImlucC1yb29tIiBwbGFjZWhvbGRlcj0iUm9vbSBjb2RlIChl"
    "LmcuIEFCQ0QpIiBtYXhsZW5ndGg9IjQiCiAgICAgICAgICAgc3R5bGU9InRleHQtdHJhbnNmb3Jt"
    "OnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzozcHg7Ij4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBi"
    "dG4tYmx1ZSIgb25jbGljaz0iam9pblJvb20oKSI+Sm9pbiBSb29tPC9idXR0b24+CiAgPC9kaXY+"
    "CgogIDxkaXYgaWQ9ImxvYmJ5LW1zZyIgc3R5bGU9ImNvbG9yOiNmY2E1YTU7Zm9udC1zaXplOi44"
    "NXJlbTtkaXNwbGF5Om5vbmU7Ij48L2Rpdj4KPC9kaXY+Cgo8IS0tIOKVkOKVkCBHQU1FIEJPQVJE"
    "IOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkCAtLT4KPGRpdiBpZD0iZ2FtZSI+CiAgPGRpdiBpZD0iZGMtYmFubmVy"
    "Ij7imqAgT3Bwb25lbnQgZGlzY29ubmVjdGVkIOKAlCB3YWl0aW5nIGZvciB0aGVtIHRvIHJlam9p"
    "buKApjwvZGl2PgoKICA8IS0tIEhlYWRlciAtLT4KICA8ZGl2IGlkPSJoZWFkZXIiPgogICAgPGRp"
    "diBjbGFzcz0icm9vbS1jb2RlIj5Sb29tIDxzcGFuIGlkPSJoZHItcm9vbSI+4oCUPC9zcGFuPjwv"
    "ZGl2PgogICAgPGRpdiBjbGFzcz0ic2NvcmUtYm94Ij4KICAgICAgPGRpdiBjbGFzcz0ic25hbWUi"
    "IGlkPSJzY29yZTAtbmFtZSI+4oCUPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN2YWwiICBpZD0i"
    "c2NvcmUwLXZhbCI+MDwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzY29yZS1ib3gi"
    "PgogICAgICA8ZGl2IGNsYXNzPSJzbmFtZSIgaWQ9InNjb3JlMS1uYW1lIj7igJQ8L2Rpdj4KICAg"
    "ICAgPGRpdiBjbGFzcz0ic3ZhbCIgIGlkPSJzY29yZTEtdmFsIj4wPC9kaXY+CiAgICA8L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9InNwYWNlciI+PC9kaXY+CiAgICA8ZGl2IGlkPSJ0dXJuLWJhbm5lciIg"
    "Y2xhc3M9InR1cm4tb3BwIj5XYWl0aW5n4oCmPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "YnRuLWdvbGQgYnRuLXNtIiBpZD0iYnRuLWRlY2xhcmUiIG9uY2xpY2s9InNlbmREZWNsYXJlKCki"
    "IHN0eWxlPSJkaXNwbGF5Om5vbmUiPuKciyBEZWNsYXJlIFdpbjwvYnV0dG9uPgogICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGJ0bi1vcmFuZ2UgYnRuLXNtIiBvbmNsaWNrPSJzZW5kUmVzZXQoKSI+8J+U"
    "hCBSZXNldDwvYnV0dG9uPgogIDwvZGl2PgoKICA8IS0tIEJvYXJkIC0tPgogIDxkaXYgaWQ9ImJv"
    "YXJkIj4KICAgIDwhLS0gT3Bwb25lbnQgaGFuZCAtLT4KICAgIDxkaXYgaWQ9Im9wcC1hcmVhIj4K"
    "ICAgICAgPGRpdiBpZD0ib3BwLW5hbWUtcm93Ij4KICAgICAgICA8ZGl2IGNsYXNzPSJzZWMtbGJs"
    "IiBpZD0ib3BwLWxhYmVsIj5PcHBvbmVudDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBp"
    "ZD0ib3BwLWhhbmQiPjwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSBUYWJsZTogZGVjayArIGRp"
    "c2NhcmQgKyBtZXNzYWdlIC0tPgogICAgPGRpdiBpZD0idGFibGUtYXJlYSI+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InBpbGUiPgogICAgICAgIDxkaXYgY2xhc3M9InBpbGUtbGFiZWwiPvCfk6YgRGVjazwv"
    "ZGl2PgogICAgICAgIDxkaXYgaWQ9ImRlY2stY2FyZCIgY2xhc3M9ImNhcmQtYmFjayBjbGlja2Fi"
    "bGUiIG9uY2xpY2s9ImRyYXdEZWNrKCkiPuKcpjwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImZv"
    "bnQtc2l6ZTouNjVyZW07Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwuMzUpO21hcmdpbi10b3A6MnB4"
    "OyIgaWQ9ImRlY2stY291bnQiPjwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "cGlsZSI+CiAgICAgICAgPGRpdiBjbGFzcz0icGlsZS1sYWJlbCI+8J+XgyBEaXNjYXJkPC9kaXY+"
    "CiAgICAgICAgPGRpdiBpZD0iZGlzY2FyZC1jYXJkIj48L2Rpdj4KICAgICAgPC9kaXY+CiAgICAg"
    "IDxkaXYgaWQ9Im1zZy1ib3giPkNvbm5lY3RpbmfigKY8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwh"
    "LS0gUGxheWVyIGhhbmQgbGFiZWwgLS0+CiAgICA8ZGl2IGlkPSJteS1hcmVhIj4KICAgICAgPGRp"
    "diBjbGFzcz0ic2VjLWxibCI+8J+nkSBZb3VyIGhhbmQgJm5ic3A7wrcmbmJzcDsgPHNwYW4gc3R5"
    "bGU9Im9wYWNpdHk6LjU7Zm9udC1zaXplOi42cmVtOyI+4oaQIOKGkiB0byByZW9yZGVyPC9zcGFu"
    "PjwvZGl2PgogICAgICA8IS0tIENhcmRzIC0tPgogICAgICA8ZGl2IGlkPSJteS1oYW5kLXJvdyI+"
    "PC9kaXY+CiAgICAgIDwhLS0gU3dhcCBidXR0b25zIC0tPgogICAgICA8ZGl2IGlkPSJzd2FwLXJv"
    "dyI+PC9kaXY+CiAgICAgIDwhLS0gRGlzY2FyZCBidXR0b25zIC0tPgogICAgICA8ZGl2IGlkPSJk"
    "aXNjYXJkLXJvdyI+PC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8IS0tIExlZ2VuZCAtLT4KICAgIDxk"
    "aXYgaWQ9ImxlZ2VuZCI+CiAgICAgIPCfqpkgT3JvcyAmbmJzcDvCtyZuYnNwOyDwn423IENvcGFz"
    "ICZuYnNwO8K3Jm5ic3A7IOKalO+4jyBFc3BhZGFzICZuYnNwO8K3Jm5ic3A7IPCfj5EgQmFzdG9z"
    "CiAgICAgICZuYnNwOyZuYnNwO3wmbmJzcDsmbmJzcDsKICAgICAgQSAyIDMgNCA1IDYgNyAxMCAx"
    "MSAxMgogICAgICAmbmJzcDsmbmJzcDt8Jm5ic3A7Jm5ic3A7CiAgICAgIEJsdWUgZ2xvdyA9IGp1"
    "c3QgZHJhd24gJm5ic3A7wrcmbmJzcDsgR29sZCBnbG93ID0gQ2hpbmNow7NuICZuYnNwO8K3Jm5i"
    "c3A7IEdyZWVuIGdsb3cgPSBTdG9wCiAgICA8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIOKV"
    "kOKVkCBSRVNVTFQgT1ZFUkxBWSDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZAgLS0+CjxkaXYgaWQ9InJlc3VsdC1vdmVybGF5Ij4KICA8"
    "ZGl2IGlkPSJyZXN1bHQtY2FyZCI+CiAgICA8ZGl2IGlkPSJyZXMtZW1vamkiIHN0eWxlPSJmb250"
    "LXNpemU6Mi41cmVtO21hcmdpbi1ib3R0b206NnB4OyI+8J+OiTwvZGl2PgogICAgPGgyIGlkPSJy"
    "ZXMtdGl0bGUiPuKAlDwvaDI+CiAgICA8ZGl2IGlkPSJyZXMtcGVuYWx0aWVzIiBjbGFzcz0icGVu"
    "YWx0eS1yb3ciIHN0eWxlPSJtYXJnaW46MTJweCAwOyI+PC9kaXY+CiAgICA8ZGl2IGlkPSJyZXMt"
    "bWVsZHMiPjwvZGl2PgogICAgPGRpdiBpZD0icmVzLXVubWF0Y2hlZCI+PC9kaXY+CiAgICA8ZGl2"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7anVzdGlmeS1jb250ZW50OmNlbnRlcjttYXJn"
    "aW4tdG9wOjE4cHg7Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1ncmVlbiIgaWQ9ImJ0"
    "bi1uZXh0IiBvbmNsaWNrPSJzZW5kTmV4dEhhbmQoKSI+4pa2IE5leHQgSGFuZDwvYnV0dG9uPgog"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLW9yYW5nZSIgb25jbGljaz0ic2VuZFJlc2V0KCki"
    "PvCflIQgTmV3IEdhbWU8L2J1dHRvbj4KICAgIDwvZGl2PgogIDwvZGl2Pgo8L2Rpdj4KCjxzY3Jp"
    "cHQ+Ci8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkAovLyAgU1ZHIENBUkQgUkVOREVSSU5HCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkApjb25zdCBDVyA9IDcyLCBD"
    "SCA9IDEwODsKCmNvbnN0IFBPUyA9IHsKICAxOiBbWzM2LDU0XV0sCiAgMjogW1szNiwzMF0sWzM2"
    "LDc4XV0sCiAgMzogW1szNiwyNF0sWzM2LDU0XSxbMzYsODRdXSwKICA0OiBbWzIyLDMyXSxbNTAs"
    "MzJdLFsyMiw3Nl0sWzUwLDc2XV0sCiAgNTogW1syMiwyNF0sWzUwLDI0XSxbMzYsNTRdLFsyMiw4"
    "NF0sWzUwLDg0XV0sCiAgNjogW1syMiwyNF0sWzUwLDI0XSxbMjIsNTRdLFs1MCw1NF0sWzIyLDg0"
    "XSxbNTAsODRdXSwKICA3OiBbWzIyLDIwXSxbNTAsMjBdLFszNiwzOF0sWzIyLDU0XSxbNTAsNTRd"
    "LFsyMiw3Nl0sWzUwLDc2XV0sCn07CmNvbnN0IFNaUyA9IHsxOjE1LDI6MTMsMzoxMiw0OjEwLDU6"
    "OSw2OjksNzo4fTsKCmZ1bmN0aW9uIGYobikgeyByZXR1cm4gKCtuKS50b0ZpeGVkKDEpOyB9Cgpm"
    "dW5jdGlvbiBzeW1Tdmcoc3VpdCwgY3gsIGN5LCBzeikgewogIGlmIChzdWl0ID09PSAnT3Jvcycp"
    "IHsKICAgIGNvbnN0IHN3ID0gTWF0aC5tYXgoMS4xLCBzeiowLjEyKTsKICAgIHJldHVybiBgPGNp"
    "cmNsZSBjeD0iJHtmKGN4KX0iIGN5PSIke2YoY3kpfSIgcj0iJHtmKHN6KX0iIGZpbGw9IiNGNkM4"
    "MjAiIHN0cm9rZT0iI0EwNzgxOCIgc3Ryb2tlLXdpZHRoPSIke2Yoc3cpfSIvPgogICAgICAgICAg"
    "ICA8Y2lyY2xlIGN4PSIke2YoY3gpfSIgY3k9IiR7ZihjeSl9IiByPSIke2Yoc3oqMC42Mil9IiBm"
    "aWxsPSJub25lIiBzdHJva2U9IiNBMDc4MTgiIHN0cm9rZS13aWR0aD0iJHtmKHN3KjAuNTUpfSIv"
    "PgogICAgICAgICAgICA8Y2lyY2xlIGN4PSIke2YoY3gpfSIgY3k9IiR7ZihjeSl9IiByPSIke2Yo"
    "c3oqMC4yNil9IiBmaWxsPSIjQTA3ODE4Ii8+YDsKICB9CiAgaWYgKHN1aXQgPT09ICdFc3BhZGFz"
    "JykgewogICAgY29uc3QgYncgPSBNYXRoLm1heCgxLjQsIHN6KjAuMTcpLCBndyA9IHN6KjAuNzI7"
    "CiAgICByZXR1cm4gYDxwb2x5Z29uIHBvaW50cz0iJHtmKGN4KX0sJHtmKGN5LXN6KX0gJHtmKGN4"
    "LWJ3KX0sJHtmKGN5LXN6KjAuMjgpfSAke2YoY3grYncpfSwke2YoY3ktc3oqMC4yOCl9IiBmaWxs"
    "PSIjNDA2OEI4Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7ZihjeC1idyl9IiB5PSIke2YoY3kt"
    "c3oqMC4yOCl9IiB3aWR0aD0iJHtmKGJ3KjIpfSIgaGVpZ2h0PSIke2Yoc3oqMC41NCl9IiBmaWxs"
    "PSIjNDA2OEI4Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7ZihjeC1ndyl9IiB5PSIke2YoY3kr"
    "c3oqMC4yNCl9IiB3aWR0aD0iJHtmKGd3KjIpfSIgaGVpZ2h0PSIke2Yoc3oqMC4xNyl9IiBmaWxs"
    "PSIjOUI2RTIyIiByeD0iMiIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2YoY3gtYncqMS42KX0i"
    "IHk9IiR7ZihjeStzeiowLjQxKX0iIHdpZHRoPSIke2YoYncqMy4yKX0iIGhlaWdodD0iJHtmKHN6"
    "KjAuNTkpfSIgZmlsbD0iIzdBNEMxOCIgcng9IjIiLz5gOwogIH0KICBpZiAoc3VpdCA9PT0gJ0Nv"
    "cGFzJykgewogICAgY29uc3QgYnc9c3oqMC44NiwgYmg9c3oqMC43MCwgeTA9Y3ktc3osIHltPXkw"
    "K2JoOwogICAgY29uc3Qgc3cyPXN6KjAuMTUsIHNoPXN6KjAuNDIsIGJhdz1zeiowLjU4OwogICAg"
    "cmV0dXJuIGA8cGF0aCBkPSJNJHtmKGN4LWJ3KX0sJHtmKHkwKX0gUSR7ZihjeC1idyl9LCR7Zih5"
    "bSl9ICR7ZihjeCl9LCR7Zih5bSl9IFEke2YoY3grYncpfSwke2YoeW0pfSAke2YoY3grYncpfSwk"
    "e2YoeTApfSBaIiBmaWxsPSIjQ0MxODAwIiBzdHJva2U9IiM4ODAwMDAiIHN0cm9rZS13aWR0aD0i"
    "MC45Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7ZihjeC1zdzIpfSIgeT0iJHtmKHltKX0iIHdp"
    "ZHRoPSIke2Yoc3cyKjIpfSIgaGVpZ2h0PSIke2Yoc2gpfSIgZmlsbD0iI0FBMTQwMCIvPgogICAg"
    "ICAgICAgICA8ZWxsaXBzZSBjeD0iJHtmKGN4KX0iIGN5PSIke2YoeW0rc2gpfSIgcng9IiR7Zihi"
    "YXcpfSIgcnk9IiR7ZihzeiowLjE3KX0iIGZpbGw9IiM4ODAwMDAiLz5gOwogIH0KICAvLyBCYXN0"
    "b3MKICBjb25zdCBhbmc9MjAqTWF0aC5QSS8xODAsIHJ4Xz1NYXRoLm1heCgzLjUsc3oqMC4zMCks"
    "IGtyPU1hdGgubWF4KDQuMCxzeiowLjQwKTsKICBjb25zdCB0eD1jeC1NYXRoLnNpbihhbmcpKnN6"
    "LCB0eT1jeS1NYXRoLmNvcyhhbmcpKnN6OwogIGNvbnN0IGJ4PWN4K01hdGguc2luKGFuZykqc3os"
    "IGJ5Xz1jeStNYXRoLmNvcyhhbmcpKnN6OwogIHJldHVybiBgPGVsbGlwc2UgY3g9IiR7ZihjeCl9"
    "IiBjeT0iJHtmKGN5KX0iIHJ4PSIke2YocnhfKX0iIHJ5PSIke2Yoc3opfSIgZmlsbD0iIzU4OTAz"
    "MCIgc3Ryb2tlPSIjMjg2MDEwIiBzdHJva2Utd2lkdGg9IjAuOCIgdHJhbnNmb3JtPSJyb3RhdGUo"
    "MjAsJHtmKGN4KX0sJHtmKGN5KX0pIi8+CiAgICAgICAgICA8Y2lyY2xlIGN4PSIke2YodHgpfSIg"
    "Y3k9IiR7Zih0eSl9IiByPSIke2Yoa3IpfSIgZmlsbD0iIzQ4NzgyMCIvPgogICAgICAgICAgPGNp"
    "cmNsZSBjeD0iJHtmKGJ4KX0iIGN5PSIke2YoYnlfKX0iIHI9IiR7Zihrcil9IiBmaWxsPSIjNDg3"
    "ODIwIi8+YDsKfQoKZnVuY3Rpb24gZmFjZUJvZHlTdmcoc3VpdCwgdikgewogIGNvbnN0IGN4PUNX"
    "LzIsIGN5PUNILzI7CiAgY29uc3QgYmFuZD17T3JvczonI0M4OTAxMCcsRXNwYWRhczonIzIwNTBB"
    "MCcsQ29wYXM6JyNBQTE0MDAnLEJhc3RvczonIzJFNjgxOCd9W3N1aXRdOwogIGxldCBvdXQgPSBg"
    "PHJlY3QgeD0iMCIgeT0iMCIgd2lkdGg9IiR7Q1d9IiBoZWlnaHQ9IjI4IiBmaWxsPSIke2JhbmR9"
    "IiBvcGFjaXR5PSIwLjM1IiByeD0iNSIvPgogICAgICAgICAgICAgPHJlY3QgeD0iMCIgeT0iJHtD"
    "SC0yOH0iIHdpZHRoPSIke0NXfSIgaGVpZ2h0PSIyOCIgZmlsbD0iJHtiYW5kfSIgb3BhY2l0eT0i"
    "MC4zNSIvPmA7CiAgaWYgKHY9PT0xMCkgeyAvLyBTb3RhCiAgICBvdXQgKz0gYDxjaXJjbGUgY3g9"
    "IiR7Y3h9IiBjeT0iJHtjeS0yNH0iIHI9IjEwIiBmaWxsPSIjRkRCRjc4IiBzdHJva2U9IiM4QjVB"
    "MjgiIHN0cm9rZS13aWR0aD0iMSIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2N4LTExfSIgeT0i"
    "JHtjeS0xNH0iIHdpZHRoPSIyMiIgaGVpZ2h0PSIzMCIgcng9IjQiIGZpbGw9IiR7YmFuZH0iLz4K"
    "ICAgICAgICAgICAgPHJlY3QgeD0iJHtjeC04fSIgeT0iJHtjeSsxNn0iIHdpZHRoPSI3IiBoZWln"
    "aHQ9IjE4IiByeD0iMyIgZmlsbD0iJHtiYW5kfSIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2N4"
    "KzF9IiB5PSIke2N5KzE2fSIgd2lkdGg9IjciIGhlaWdodD0iMTgiIHJ4PSIzIiBmaWxsPSIke2Jh"
    "bmR9Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7Y3gtMTh9IiB5PSIke2N5LTExfSIgd2lkdGg9"
    "IjgiIGhlaWdodD0iMTQiIHJ4PSIzIiBmaWxsPSIke2JhbmR9Ii8+CiAgICAgICAgICAgIDxyZWN0"
    "IHg9IiR7Y3grMTB9IiB5PSIke2N5LTExfSIgd2lkdGg9IjgiIGhlaWdodD0iMTQiIHJ4PSIzIiBm"
    "aWxsPSIke2JhbmR9Ii8+YDsKICB9IGVsc2UgaWYgKHY9PT0xMSkgeyAvLyBDYWJhbGxvCiAgICBv"
    "dXQgKz0gYDxlbGxpcHNlIGN4PSIke2N4fSIgY3k9IiR7Y3krMTZ9IiByeD0iMTkiIHJ5PSIxMCIg"
    "ZmlsbD0iI0QwQTA3MCIvPgogICAgICAgICAgICA8Y2lyY2xlIGN4PSIke2N4KzE2fSIgY3k9IiR7"
    "Y3krNn0iIHI9IjciIGZpbGw9IiNEMEEwNzAiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeC0x"
    "NX0iIHk9IiR7Y3krMjR9IiB3aWR0aD0iNSIgaGVpZ2h0PSIxNCIgcng9IjIiIGZpbGw9IiNCMDgw"
    "NTAiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeC02fSIgeT0iJHtjeSsyNH0iIHdpZHRoPSI1"
    "IiBoZWlnaHQ9IjE0IiByeD0iMiIgZmlsbD0iI0IwODA1MCIvPgogICAgICAgICAgICA8cmVjdCB4"
    "PSIke2N4KzR9IiB5PSIke2N5KzI0fSIgd2lkdGg9IjUiIGhlaWdodD0iMTQiIHJ4PSIyIiBmaWxs"
    "PSIjQjA4MDUwIi8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7Y3grMTJ9IiB5PSIke2N5KzI0fSIg"
    "d2lkdGg9IjUiIGhlaWdodD0iMTQiIHJ4PSIyIiBmaWxsPSIjQjA4MDUwIi8+CiAgICAgICAgICAg"
    "IDxjaXJjbGUgY3g9IiR7Y3gtMn0iIGN5PSIke2N5LTE2fSIgcj0iOSIgZmlsbD0iI0ZEQkY3OCIg"
    "c3Ryb2tlPSIjOEI1QTI4IiBzdHJva2Utd2lkdGg9IjEiLz4KICAgICAgICAgICAgPHJlY3QgeD0i"
    "JHtjeC0xMH0iIHk9IiR7Y3ktN30iIHdpZHRoPSIxOSIgaGVpZ2h0PSIyMiIgcng9IjMiIGZpbGw9"
    "IiR7YmFuZH0iLz5gOwogIH0gZWxzZSB7IC8vIFJleQogICAgb3V0ICs9IGA8Y2lyY2xlIGN4PSIk"
    "e2N4fSIgY3k9IiR7Y3ktMjJ9IiByPSIxMCIgZmlsbD0iI0ZEQkY3OCIgc3Ryb2tlPSIjOEI1QTI4"
    "IiBzdHJva2Utd2lkdGg9IjEiLz4KICAgICAgICAgICAgPHBvbHlnb24gcG9pbnRzPSIke2N4LTEw"
    "fSwke2N5LTMxfSAke2N4LTEwfSwke2N5LTQwfSAke2N4LTR9LCR7Y3ktMzN9ICR7Y3h9LCR7Y3kt"
    "NDJ9ICR7Y3grNH0sJHtjeS0zM30gJHtjeCsxMH0sJHtjeS00MH0gJHtjeCsxMH0sJHtjeS0zMX0i"
    "IGZpbGw9IiNGOEM4MjAiIHN0cm9rZT0iI0EwNzAxMCIgc3Ryb2tlLXdpZHRoPSIxLjIiLz4KICAg"
    "ICAgICAgICAgPGNpcmNsZSBjeD0iJHtjeH0iIGN5PSIke2N5LTM3fSIgcj0iMy41IiBmaWxsPSIj"
    "Q0MyMDIwIi8+CiAgICAgICAgICAgIDxwb2x5Z29uIHBvaW50cz0iJHtjeC0xNX0sJHtjeS0xMn0g"
    "JHtjeCsxNX0sJHtjeS0xMn0gJHtjeCsxOX0sJHtjeSszNn0gJHtjeC0xOX0sJHtjeSszNn0iIGZp"
    "bGw9IiR7YmFuZH0iLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeC0xNH0iIHk9IiR7Y3krMn0i"
    "IHdpZHRoPSIyOCIgaGVpZ2h0PSI2IiBmaWxsPSIjRjhDODIwIi8+CiAgICAgICAgICAgIDxyZWN0"
    "IHg9IiR7Y3gtMjJ9IiB5PSIke2N5LTEwfSIgd2lkdGg9IjkiIGhlaWdodD0iMTYiIHJ4PSIzIiBm"
    "aWxsPSIke2JhbmR9Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7Y3grMTN9IiB5PSIke2N5LTEw"
    "fSIgd2lkdGg9IjkiIGhlaWdodD0iMTYiIHJ4PSIzIiBmaWxsPSIke2JhbmR9Ii8+YDsKICB9CiAg"
    "b3V0ICs9IHN5bVN2ZyhzdWl0LCBjeCwgY3krMzAsIDkpOwogIHJldHVybiBvdXQ7Cn0KCmZ1bmN0"
    "aW9uIGNhcmRTdmdJbm5lcihjKSB7CiAgaWYgKGMudiA+PSAxMCkgcmV0dXJuIGZhY2VCb2R5U3Zn"
    "KGMucywgYy52KTsKICByZXR1cm4gKFBPU1tjLnZdfHxbWzM2LDU0XV0pLm1hcCgoW3B4LHB5XSkg"
    "PT4gc3ltU3ZnKGMucywgcHgsIHB5LCBTWlNbYy52XXx8OSkpLmpvaW4oJycpOwp9CgpmdW5jdGlv"
    "biByZW5kZXJDYXJkKGMsIG9wdHM9e30pIHsKICAvLyBvcHRzOiB7Y2xpY2thYmxlLCBnbG93Q2xh"
    "c3MsIHNtYWxsfQogIGNvbnN0IGdsb3dDbGFzcyA9IG9wdHMuZ2xvd0NsYXNzIHx8ICcnOwogIGNv"
    "bnN0IGNsaWNrQ2xzICA9IG9wdHMuY2xpY2thYmxlID8gJyBjbGlja2FibGUnIDogJyc7CiAgY29u"
    "c3QgYm9keSA9IGNhcmRTdmdJbm5lcihjKTsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQtc3Zn"
    "LW91dGVyJHtnbG93Q2xhc3MgPyAnICcrZ2xvd0NsYXNzIDogJyd9JHtjbGlja0Nsc30iPgogICAg"
    "PHN2ZyB3aWR0aD0iJHtDV30iIGhlaWdodD0iJHtDSH0iIHhtbG5zPSJodHRwOi8vd3d3LnczLm9y"
    "Zy8yMDAwL3N2ZyIKICAgICAgICAgc3R5bGU9InBvc2l0aW9uOmFic29sdXRlO3RvcDowO2xlZnQ6"
    "MDsiPiR7Ym9keX08L3N2Zz4KICAgIDxzcGFuIGNsYXNzPSJjYXJkLWNvcm5lciB0bCIgc3R5bGU9"
    "ImNvbG9yOiR7Yy5jb2xvcn0iPiR7Yy5sYWJlbH08L3NwYW4+CiAgICA8c3BhbiBjbGFzcz0iY2Fy"
    "ZC1jb3JuZXIgYnIiIHN0eWxlPSJjb2xvcjoke2MuY29sb3J9Ij4ke2MubGFiZWx9PC9zcGFuPgog"
    "IDwvZGl2PmA7Cn0KCmZ1bmN0aW9uIHJlbmRlckJhY2soKSB7CiAgcmV0dXJuIGA8ZGl2IGNsYXNz"
    "PSJjYXJkLWJhY2siPuKcpjwvZGl2PmA7Cn0KCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyAgV0VCU09DS0VUIENMSUVOVAov"
    "LyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZAKbGV0IHdzID0gbnVsbDsKbGV0IG15UGxheWVyID0gLTE7CmxldCByb29tQ29kZSA9ICcn"
    "OwpsZXQgc3RhdGUgICAgPSBudWxsOwoKZnVuY3Rpb24gd3NVcmwoKSB7CiAgY29uc3QgcHJvdG8g"
    "PSBsb2NhdGlvbi5wcm90b2NvbCA9PT0gJ2h0dHBzOicgPyAnd3NzJyA6ICd3cyc7CiAgcmV0dXJu"
    "IGAke3Byb3RvfTovLyR7bG9jYXRpb24uaG9zdH0vd3NgOwp9CgpmdW5jdGlvbiBjb25uZWN0KGZp"
    "cnN0TXNnKSB7CiAgd3MgPSBuZXcgV2ViU29ja2V0KHdzVXJsKCkpOwogIHdzLm9ub3BlbiA9ICgp"
    "ID0+IHsKICAgIHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkoZmlyc3RNc2cpKTsKICAgIGNsZWFyTG9i"
    "YnlFcnJvcigpOwogIH07CiAgd3Mub25tZXNzYWdlID0gZSA9PiBoYW5kbGVNc2coSlNPTi5wYXJz"
    "ZShlLmRhdGEpKTsKICB3cy5vbmNsb3NlID0gKCkgPT4gewogICAgaWYgKG15UGxheWVyID49IDAp"
    "IHNob3dEY0Jhbm5lcih0cnVlKTsKICAgIHNldFRpbWVvdXQoKCkgPT4gcmVjb25uZWN0KCksIDMw"
    "MDApOwogIH07CiAgd3Mub25lcnJvciA9ICgpID0+IHt9Owp9CgpmdW5jdGlvbiByZWNvbm5lY3Qo"
    "KSB7CiAgaWYgKHJvb21Db2RlICYmIG15UGxheWVyID49IDApIHsKICAgIGNvbnN0IG5hbWUgPSBk"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgnaW5wLW5hbWUnKS52YWx1ZS50cmltKCkgfHwgJ1BsYXll"
    "cic7CiAgICBjb25uZWN0KHsgYWN0aW9uOidqb2luJywgcm9vbTogcm9vbUNvZGUsIG5hbWUgfSk7"
    "CiAgfQp9CgpmdW5jdGlvbiBzZW5kKG9iaikgewogIGlmICh3cyAmJiB3cy5yZWFkeVN0YXRlID09"
    "PSBXZWJTb2NrZXQuT1BFTikgd3Muc2VuZChKU09OLnN0cmluZ2lmeShvYmopKTsKfQoKLy8g4pSA"
    "4pSAIExvYmJ5IGFjdGlvbnMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGdldE5hbWUoKSB7CiAgcmV0dXJuIChkb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgnaW5wLW5hbWUnKS52YWx1ZS50cmltKCkgfHwgJ1BsYXllcicp"
    "LnNsaWNlKDAsMjApOwp9CmZ1bmN0aW9uIHNob3dMb2JieUVycm9yKG1zZykgewogIGNvbnN0IGVs"
    "ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xvYmJ5LW1zZycpOwogIGVsLnRleHRDb250ZW50"
    "ID0gbXNnOyBlbC5zdHlsZS5kaXNwbGF5PSdibG9jayc7Cn0KZnVuY3Rpb24gY2xlYXJMb2JieUVy"
    "cm9yKCkgewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2JieS1tc2cnKS5zdHlsZS5kaXNw"
    "bGF5PSdub25lJzsKfQpmdW5jdGlvbiBjcmVhdGVSb29tKCkgeyBjb25uZWN0KHsgYWN0aW9uOidj"
    "cmVhdGUnLCBuYW1lOiBnZXROYW1lKCkgfSk7IH0KZnVuY3Rpb24gam9pblJvb20oKSAgIHsKICBj"
    "b25zdCBjb2RlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2lucC1yb29tJykudmFsdWUudG9V"
    "cHBlckNhc2UoKS50cmltKCk7CiAgaWYgKCFjb2RlKSB7IHNob3dMb2JieUVycm9yKCdFbnRlciBh"
    "IHJvb20gY29kZS4nKTsgcmV0dXJuOyB9CiAgY29ubmVjdCh7IGFjdGlvbjonam9pbicsIHJvb206"
    "Y29kZSwgbmFtZTogZ2V0TmFtZSgpIH0pOwp9CgovLyDilIDilIAgR2FtZSBhY3Rpb25zIOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gApmdW5jdGlvbiBkcmF3RGVjaygpICAgICB7IHNlbmQoe2FjdGlvbjonZHJhd19kZWNrJ30pOyB9"
    "CmZ1bmN0aW9uIGRyYXdEaXNjYXJkKCkgIHsgc2VuZCh7YWN0aW9uOidkcmF3X2Rpc2NhcmQnfSk7"
    "IH0KZnVuY3Rpb24gZGlzY2FyZChpZHgpICAgeyBzZW5kKHthY3Rpb246J2Rpc2NhcmQnLCBpZHh9"
    "KTsgfQpmdW5jdGlvbiBzZW5kTW92ZShpLGopICB7IHNlbmQoe2FjdGlvbjonbW92ZScsIGksIGp9"
    "KTsgfQpmdW5jdGlvbiBzZW5kRGVjbGFyZSgpICB7IHNlbmQoe2FjdGlvbjonZGVjbGFyZSd9KTsg"
    "fQpmdW5jdGlvbiBzZW5kTmV4dEhhbmQoKSB7IGNsb3NlUmVzdWx0KCk7IHNlbmQoe2FjdGlvbjon"
    "bmV4dF9oYW5kJ30pOyB9CmZ1bmN0aW9uIHNlbmRSZXNldCgpICAgIHsgY2xvc2VSZXN1bHQoKTsg"
    "c2VuZCh7YWN0aW9uOidyZXNldCd9KTsgfQoKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCi8vICBNRVNTQUdFIEhBTkRMRVIKLy8g"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQCmZ1bmN0aW9uIGhhbmRsZU1zZyhtc2cpIHsKICBzaG93RGNCYW5uZXIoZmFsc2UpOwoKICBp"
    "ZiAobXNnLnR5cGUgPT09ICdjcmVhdGVkJykgewogICAgbXlQbGF5ZXIgPSAwOwogICAgcm9vbUNv"
    "ZGUgPSBtc2cucm9vbTsKICAgIHNob3dHYW1lKCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgnaGRyLXJvb20nKS50ZXh0Q29udGVudCA9IG1zZy5yb29tOwogICAgZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoJ21zZy1ib3gnKS50ZXh0Q29udGVudCA9CiAgICAgIGBSb29tICR7bXNnLnJvb219"
    "IGNyZWF0ZWQhIFdhaXRpbmcgZm9yIG9wcG9uZW504oCmYDsKICAgIHJldHVybjsKICB9CiAgaWYg"
    "KG1zZy50eXBlID09PSAnam9pbmVkJykgewogICAgbXlQbGF5ZXIgPSBtc2cucGxheWVyOwogICAg"
    "cm9vbUNvZGUgPSBtc2cucm9vbTsKICAgIHNob3dHYW1lKCk7CiAgICBkb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgnaGRyLXJvb20nKS50ZXh0Q29udGVudCA9IG1zZy5yb29tOwogICAgcmV0dXJuOwog"
    "IH0KICBpZiAobXNnLnR5cGUgPT09ICdsb2JieScpIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCdoZHItcm9vbScpLnRleHRDb250ZW50ID0gbXNnLnJvb207CiAgICBpZiAobXNnLndhaXRp"
    "bmcpIHsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21zZy1ib3gnKS50ZXh0Q29udGVu"
    "dCA9CiAgICAgICAgYFJvb20gJHttc2cucm9vbX0g4oCUIHdhaXRpbmcgZm9yIG9wcG9uZW504oCm"
    "YDsKICAgIH0KICAgIHJldHVybjsKICB9CiAgaWYgKG1zZy50eXBlID09PSAnZXJyb3InKSB7IHNo"
    "b3dMb2JieUVycm9yKG1zZy5tc2cpOyByZXR1cm47IH0KICBpZiAobXNnLnR5cGUgPT09ICdwbGF5"
    "ZXJfbGVmdCcpIHsKICAgIHNob3dEY0Jhbm5lcih0cnVlLCBtc2cubXNnKTsgcmV0dXJuOwogIH0K"
    "ICBpZiAobXNnLnR5cGUgPT09ICdwb25nJykgcmV0dXJuOwogIGlmIChtc2cudHlwZSA9PT0gJ3N0"
    "YXRlJykgewogICAgc3RhdGUgPSBtc2c7CiAgICByZW5kZXJTdGF0ZShtc2cpOwogICAgcmV0dXJu"
    "OwogIH0KfQoKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQCi8vICBSRU5ERVIKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCmZ1bmN0aW9uIHNob3dHYW1lKCkgewog"
    "IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2JieScpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7"
    "CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dhbWUnKS5zdHlsZS5kaXNwbGF5ICA9ICdmbGV4"
    "JzsKfQoKZnVuY3Rpb24gc2NvcmVDb2xvcihwdHMpIHsKICBpZiAocHRzID49IDgwKSByZXR1cm4g"
    "J3MtcmVkJzsKICBpZiAocHRzID49IDUwKSByZXR1cm4gJ3Mtb3JhbmdlJzsKICByZXR1cm4gJ3Mt"
    "Z3JlZW4nOwp9CgpmdW5jdGlvbiByZW5kZXJTdGF0ZShzKSB7CiAgLy8gU2NvcmVzCiAgY29uc3Qg"
    "bmFtZXMgPSBzLm5hbWVzOwogIGZvciAobGV0IGk9MDtpPDI7aSsrKSB7CiAgICBjb25zdCBlbCA9"
    "IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGBzY29yZSR7aX0tdmFsYCk7CiAgICBlbC50ZXh0Q29u"
    "dGVudCA9IHMuc2NvcmVzW2ldOwogICAgZWwuY2xhc3NOYW1lID0gYHN2YWwgJHtzY29yZUNvbG9y"
    "KHMuc2NvcmVzW2ldKX1gOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoYHNjb3JlJHtpfS1u"
    "YW1lYCkudGV4dENvbnRlbnQgPSBuYW1lc1tpXSB8fCAn4oCUJzsKICB9CgogIC8vIFR1cm4gYmFu"
    "bmVyCiAgY29uc3QgdGIgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndHVybi1iYW5uZXInKTsK"
    "ICBpZiAocy5teV90dXJuKSB7CiAgICB0Yi50ZXh0Q29udGVudCA9ICfwn5+iIFlvdXIgdHVybic7"
    "CiAgICB0Yi5jbGFzc05hbWUgPSAndHVybi1taW5lJzsKICB9IGVsc2UgewogICAgdGIudGV4dENv"
    "bnRlbnQgPSBg4o+zICR7bmFtZXNbMS1teVBsYXllcl19J3MgdHVybmA7CiAgICB0Yi5jbGFzc05h"
    "bWUgPSAndHVybi1vcHAnOwogIH0KCiAgLy8gRGVjbGFyZSBXaW4gYnV0dG9uCiAgY29uc3QgYnRu"
    "RGVjbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdidG4tZGVjbGFyZScpOwogIGJ0bkRlY2wu"
    "c3R5bGUuZGlzcGxheSA9IHMuY2FuX2RlY2xhcmUgPyAnJyA6ICdub25lJzsKCiAgLy8gTWVzc2Fn"
    "ZQogIGNvbnN0IG1zZ0JveCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtc2ctYm94Jyk7CiAg"
    "bXNnQm94LnRleHRDb250ZW50ID0gcy5tZXNzYWdlOwogIG1zZ0JveC5jbGFzc05hbWUgPSAnbXNn"
    "LWJveCc7CiAgaWYgKHMucmVzdWx0KSB7CiAgICBpZiAocy5yZXN1bHQuaW5jbHVkZXMoYHAke215"
    "UGxheWVyfWApKSBtc2dCb3guY2xhc3NMaXN0LmFkZChzLnJlc3VsdC5pbmNsdWRlcygnY2MnKSA/"
    "ICdtc2ctY2MnIDogJ21zZy13aW4nKTsKICAgIGVsc2UgbXNnQm94LmNsYXNzTGlzdC5hZGQoJ21z"
    "Zy1sb3NlJyk7CiAgfQoKICAvLyBPcHBvbmVudCBsYWJlbCArIGhhbmQKICBjb25zdCBvcHBJZHgg"
    "PSBzLm9wcF9pZHg7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ29wcC1sYWJlbCcpLnRleHRD"
    "b250ZW50ID0KICAgIGDwn6SWICR7bmFtZXNbb3BwSWR4XX0gKCR7cy5vcHBfaGFuZC5sZW5ndGh9"
    "IGNhcmRzKWA7CiAgY29uc3Qgb3BwSGFuZEVsID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ29w"
    "cC1oYW5kJyk7CiAgb3BwSGFuZEVsLmlubmVySFRNTCA9ICcnOwogIGZvciAoY29uc3QgYyBvZiBz"
    "Lm9wcF9oYW5kKSB7CiAgICBpZiAoYy5mYWNlZG93bikgewogICAgICBvcHBIYW5kRWwuaW5zZXJ0"
    "QWRqYWNlbnRIVE1MKCdiZWZvcmVlbmQnLCByZW5kZXJCYWNrKCkpOwogICAgfSBlbHNlIHsKICAg"
    "ICAgY29uc3QgZ2MgPSBjLmlzX3dpbiA/ICdnbG93LW1lbGQnIDogJyc7CiAgICAgIG9wcEhhbmRF"
    "bC5pbnNlcnRBZGphY2VudEhUTUwoJ2JlZm9yZWVuZCcsIHJlbmRlckNhcmQoYywge2dsb3dDbGFz"
    "czogZ2N9KSk7CiAgICB9CiAgfQoKICAvLyBEZWNrIGNhcmQgKGNsaWNrYWJsZSBvbmx5IG9uIHlv"
    "dXIgZHJhdyB0dXJuKQogIGNvbnN0IGNhbkRyYXdEZWNrID0gcy5teV90dXJuICYmIHMucGhhc2Ug"
    "PT09ICdkcmF3JzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZGVjay1jYXJkJykuY2xhc3NO"
    "YW1lID0KICAgIGBjYXJkLWJhY2ske2NhbkRyYXdEZWNrID8gJyBjbGlja2FibGUnIDogJyd9YDsK"
    "ICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZGVjay1jYXJkJykub25jbGljayA9IGNhbkRyYXdE"
    "ZWNrID8gZHJhd0RlY2sgOiBudWxsOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkZWNrLWNv"
    "dW50JykudGV4dENvbnRlbnQgPSBgJHtzLmRlY2tfc2l6ZX0gY2FyZHNgOwoKICAvLyBEaXNjYXJk"
    "IHBpbGUgdG9wCiAgY29uc3QgZGlzY0VsID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2Rpc2Nh"
    "cmQtY2FyZCcpOwogIGlmIChzLmRpc2NhcmRfdG9wKSB7CiAgICBjb25zdCBjYW5UYWtlID0gcy5t"
    "eV90dXJuICYmIHMucGhhc2UgPT09ICdkcmF3JzsKICAgIGNvbnN0IGdjID0gJyc7CiAgICBkaXNj"
    "RWwuaW5uZXJIVE1MID0gYDxkaXYgb25jbGljaz0iJHtjYW5UYWtlID8gJ2RyYXdEaXNjYXJkKCkn"
    "IDogJyd9IiBzdHlsZT0iY3Vyc29yOiR7Y2FuVGFrZT8ncG9pbnRlcic6J2RlZmF1bHQnfSI+YCAr"
    "CiAgICAgICAgICAgICAgICAgICAgICAgIHJlbmRlckNhcmQocy5kaXNjYXJkX3RvcCwge2NsaWNr"
    "YWJsZTogY2FuVGFrZX0pICsgJzwvZGl2Pic7CiAgfSBlbHNlIHsKICAgIGRpc2NFbC5pbm5lckhU"
    "TUwgPSBgPGRpdiBzdHlsZT0id2lkdGg6NzJweDtoZWlnaHQ6MTA4cHg7Ym9yZGVyOjFweCBkYXNo"
    "ZWQgcmdiYSgyNTUsMjU1LDI1NSwuMik7Ym9yZGVyLXJhZGl1czo3cHg7ZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2NvbG9yOnJnYmEoMjU1LDI1"
    "NSwyNTUsLjI1KTtmb250LXNpemU6Ljc1cmVtOyI+RW1wdHk8L2Rpdj5gOwogIH0KCiAgLy8gTXkg"
    "aGFuZAogIHJlbmRlck15SGFuZChzKTsKCiAgLy8gUmVzdWx0IG92ZXJsYXkKICBjb25zdCBmaW5p"
    "c2hlZCA9IFsnaGFuZF9vdmVyJywnbWF0Y2hfb3ZlciddLmluY2x1ZGVzKHMucGhhc2UpOwogIGlm"
    "IChmaW5pc2hlZCkgc2hvd1Jlc3VsdChzKTsKfQoKZnVuY3Rpb24gcmVuZGVyTXlIYW5kKHMpIHsK"
    "ICBjb25zdCBoYW5kICAgICAgID0gcy5teV9oYW5kOwogIGNvbnN0IHBoYXNlICAgICAgPSBzLnBo"
    "YXNlOwogIGNvbnN0IG15VHVybiAgICAgPSBzLm15X3R1cm47CiAgY29uc3QgY2FuRGlzY2FyZCA9"
    "IG15VHVybiAmJiBwaGFzZSA9PT0gJ2Rpc2NhcmQnOwogIGNvbnN0IGNhbk1vdmUgICAgPSBteVR1"
    "cm4gJiYgWydkcmF3JywnZGlzY2FyZCddLmluY2x1ZGVzKHBoYXNlKTsKICBjb25zdCB3aW5TZXQg"
    "ICAgID0gbmV3IFNldChzLndpbl9pZHgubWFwKChbaV0pID0+IGkpKTsKICBjb25zdCB3aW5DQyAg"
    "ICAgID0gbmV3IFNldChzLndpbl9pZHguZmlsdGVyKChbLGNjXSkgPT4gY2MpLm1hcCgoW2ldKSA9"
    "PiBpKSk7CgogIGNvbnN0IGhhbmRSb3cgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXkt"
    "aGFuZC1yb3cnKTsKICBjb25zdCBzd2FwUm93ICAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "J3N3YXAtcm93Jyk7CiAgY29uc3QgZGlzY1JvdyAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCdkaXNjYXJkLXJvdycpOwogIGhhbmRSb3cuaW5uZXJIVE1MID0gJyc7CiAgc3dhcFJvdy5pbm5l"
    "ckhUTUwgPSAnJzsKICBkaXNjUm93LmlubmVySFRNTCA9ICcnOwoKICBoYW5kLmZvckVhY2goKGMs"
    "IGkpID0+IHsKICAgIC8vIENhcmQgZ2xvdwogICAgbGV0IGdjID0gJyc7CiAgICBpZiAoYy5pc19u"
    "ZXcpIGdjID0gJ2dsb3ctbmV3JzsKICAgIGVsc2UgaWYgKGMuaXNfY2MpIGdjID0gJ2dsb3ctY2Mn"
    "OwogICAgZWxzZSBpZiAoYy5pc193aW4pIGdjID0gJ2dsb3ctd2luJzsKCiAgICBoYW5kUm93Lmlu"
    "c2VydEFkamFjZW50SFRNTCgnYmVmb3JlZW5kJywKICAgICAgYDxkaXYgY2xhc3M9ImNhcmQtd3Jh"
    "cCI+JHtyZW5kZXJDYXJkKGMsIHtnbG93Q2xhc3M6IGdjfSl9PC9kaXY+YCk7CiAgfSk7CgogIC8v"
    "IFN3YXAgYnV0dG9ucyByb3cKICBoYW5kLmZvckVhY2goKGMsIGkpID0+IHsKICAgIGNvbnN0IGxE"
    "aXMgPSAhY2FuTW92ZSB8fCBpID09PSAwOwogICAgY29uc3QgckRpcyA9ICFjYW5Nb3ZlIHx8IGkg"
    "PT09IGhhbmQubGVuZ3RoIC0gMTsKICAgIGNvbnN0IGNvbCA9IGRvY3VtZW50LmNyZWF0ZUVsZW1l"
    "bnQoJ2RpdicpOwogICAgY29sLmNsYXNzTmFtZSA9ICdjYXJkLWJ0bi1jb2wnOwogICAgY29sLmlu"
    "bmVySFRNTCA9IGA8ZGl2IGNsYXNzPSJzd2FwLXBhaXIiPgogICAgICA8YnV0dG9uIGNsYXNzPSJt"
    "aW5pLWJ0biIgJHtsRGlzPydkaXNhYmxlZCc6Jyd9IG9uY2xpY2s9InNlbmRNb3ZlKCR7aX0sJHtp"
    "LTF9KSI+4oaQPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9Im1pbmktYnRuIiAke3JEaXM/"
    "J2Rpc2FibGVkJzonJ30gb25jbGljaz0ic2VuZE1vdmUoJHtpfSwke2krMX0pIj7ihpI8L2J1dHRv"
    "bj4KICAgIDwvZGl2PmA7CiAgICBzd2FwUm93LmFwcGVuZENoaWxkKGNvbCk7CiAgfSk7CgogIC8v"
    "IERpc2NhcmQgYnV0dG9ucyByb3cgKG9ubHkgaW4gZGlzY2FyZCBwaGFzZSkKICBpZiAoY2FuRGlz"
    "Y2FyZCkgewogICAgaGFuZC5mb3JFYWNoKChjLCBpKSA9PiB7CiAgICAgIGNvbnN0IGNvbCA9IGRv"
    "Y3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpOwogICAgICBjb25zdCBpc0NDICA9IHdpbkNDLmhh"
    "cyhpKTsKICAgICAgY29uc3QgaXNXaW4gPSB3aW5TZXQuaGFzKGkpOwogICAgICBsZXQgY2xzID0g"
    "JycsIGxibCA9ICfihpMnOwogICAgICBpZiAoaXNDQykgICAgICAgeyBjbHMgPSAnZC1jaGluY2hv"
    "bic7IGxibCA9ICfwn4+FJzsgfQogICAgICBlbHNlIGlmIChpc1dpbikgeyBjbHMgPSAnZC1zdG9w"
    "JzsgICAgIGxibCA9ICfinIsnOyB9CiAgICAgIGNvbC5pbm5lckhUTUwgPQogICAgICAgIGA8YnV0"
    "dG9uIGNsYXNzPSJkaXNjLWJ0biAke2Nsc30iIG9uY2xpY2s9ImRpc2NhcmQoJHtpfSkiPiR7bGJs"
    "fTwvYnV0dG9uPmA7CiAgICAgIGRpc2NSb3cuYXBwZW5kQ2hpbGQoY29sKTsKICAgIH0pOwogIH0K"
    "fQoKLy8g4pSA4pSAIFJlc3VsdCBvdmVybGF5IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzaG93UmVzdWx0KHMpIHsKICBj"
    "b25zdCByZXN1bHQgPSBzLnJlc3VsdCB8fCAnJzsKICBjb25zdCBteVdpbiAgPSByZXN1bHQuaW5j"
    "bHVkZXMoYHAke215UGxheWVyfWApICYmICFyZXN1bHQuaW5jbHVkZXMoJ2RlY2xhcmUnKTsKICBj"
    "b25zdCBpc0NDICAgPSByZXN1bHQuaW5jbHVkZXMoJ19jYycpOwogIGNvbnN0IGlzRGNsICA9IHJl"
    "c3VsdCA9PT0gJ2RlY2xhcmUnOwogIGNvbnN0IGlzTWF0Y2hPdmVyID0gcy5waGFzZSA9PT0gJ21h"
    "dGNoX292ZXInOwoKICBjb25zdCBvdmVybGF5ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Jl"
    "c3VsdC1vdmVybGF5Jyk7CiAgb3ZlcmxheS5zdHlsZS5kaXNwbGF5ID0gJ2ZsZXgnOwoKICAvLyBF"
    "bW9qaSArIHRpdGxlCiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Jlcy1lbW9qaScpLnRleHRD"
    "b250ZW50ID0KICAgIGlzQ0MgPyAn8J+PhScgOiBteVdpbiA/ICfwn46JJyA6IGlzRGNsID8gJ/Cf"
    "l5LvuI8nIDogJ/CfmJQnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZXMtdGl0bGUnKS50"
    "ZXh0Q29udGVudCA9IHMubWVzc2FnZTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncmVzLXRp"
    "dGxlJykuc3R5bGUuY29sb3IgPQogICAgaXNDQyA/ICcjZmJiZjI0JyA6IG15V2luID8gJyM4NmVm"
    "YWMnIDogaXNEY2wgPyAnI2ZiOTIzYycgOiAnI2ZjYTVhNSc7CgogIC8vIFBlbmFsdGllcwogIGNv"
    "bnN0IHBlbkRpdiA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZXMtcGVuYWx0aWVzJyk7CiAg"
    "cGVuRGl2LmlubmVySFRNTCA9ICcnOwogIGNvbnN0IHBlbnMgPSBzLnBlbmFsdGllcyB8fCBbbnVs"
    "bCwgbnVsbF07CiAgWzAsMV0uZm9yRWFjaChpID0+IHsKICAgIGlmIChwZW5zW2ldID09PSBudWxs"
    "IHx8IHBlbnNbaV0gPT09ICdjYycpIHJldHVybjsKICAgIGNvbnN0IGJveCA9IGRvY3VtZW50LmNy"
    "ZWF0ZUVsZW1lbnQoJ2RpdicpOwogICAgYm94LmNsYXNzTmFtZSA9ICdwZW4tYm94JzsKICAgIGNv"
    "bnN0IHZhbCA9IHBlbnNbaV07CiAgICBjb25zdCBjb2wgPSB2YWwgPCAwID8gJyM4NmVmYWMnIDog"
    "dmFsID09PSAwID8gJ3JnYmEoMjU1LDI1NSwyNTUsLjUpJyA6ICcjZmNhNWE1JzsKICAgIGNvbnN0"
    "IHNpZ24gPSB2YWwgPiAwID8gJysnIDogJyc7CiAgICBib3guaW5uZXJIVE1MID0gYDxkaXYgY2xh"
    "c3M9InBlbi1uYW1lIj4ke3MubmFtZXNbaV19PC9kaXY+CiAgICAgICAgICAgICAgICAgICAgIDxk"
    "aXYgY2xhc3M9InBlbi12YWwiIHN0eWxlPSJjb2xvcjoke2NvbH0iPiR7c2lnbn0ke3ZhbH0gcHRz"
    "PC9kaXY+YDsKICAgIHBlbkRpdi5hcHBlbmRDaGlsZChib3gpOwogIH0pOwoKICAvLyBNZWxkcwog"
    "IGNvbnN0IG1lbGRzRGl2ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Jlcy1tZWxkcycpOwog"
    "IG1lbGRzRGl2LmlubmVySFRNTCA9ICcnOwogIGlmIChzLm1lbGRzICYmIHMubWVsZHMubGVuZ3Ro"
    "KSB7CiAgICBmb3IgKGNvbnN0IG0gb2Ygcy5tZWxkcykgewogICAgICBjb25zdCBibG9jayA9IGRv"
    "Y3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpOwogICAgICBibG9jay5jbGFzc05hbWUgPSAnbWVs"
    "ZC1ibG9jayc7CiAgICAgIGJsb2NrLmlubmVySFRNTCA9IGA8ZGl2IGNsYXNzPSJtZWxkLWxhYmVs"
    "Ij4ke20ua2luZH08L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJtZWxkLXJvdyI+JHttLmNhcmRz"
    "Lm1hcChjID0+IHJlbmRlckNhcmQoYykpLmpvaW4oJycpfTwvZGl2PmA7CiAgICAgIG1lbGRzRGl2"
    "LmFwcGVuZENoaWxkKGJsb2NrKTsKICAgIH0KICB9CgogIC8vIFVubWF0Y2hlZCBjYXJkcwogIGNv"
    "bnN0IHVubURpdiA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZXMtdW5tYXRjaGVkJyk7CiAg"
    "dW5tRGl2LmlubmVySFRNTCA9ICcnOwogIGNvbnN0IGFkZFVubSA9IChjYXJkcywgd2hvLCBwdHMp"
    "ID0+IHsKICAgIGlmICghY2FyZHMgfHwgY2FyZHMubGVuZ3RoID09PSAwKSByZXR1cm47CiAgICBj"
    "b25zdCBibG9jayA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpOwogICAgYmxvY2suY2xh"
    "c3NOYW1lID0gJ21lbGQtYmxvY2snOwogICAgYmxvY2suaW5uZXJIVE1MID0gYDxkaXYgY2xhc3M9"
    "Im1lbGQtbGFiZWwiPiR7d2hvfSB1bm1hdGNoZWQgKCske3B0c30gcHRzKTwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJtZWxkLXJvdyI+JHtjYXJkcy5tYXAoYyA9PiByZW5kZXJDYXJkKGMpKS5qb2lu"
    "KCcnKX08L2Rpdj5gOwogICAgdW5tRGl2LmFwcGVuZENoaWxkKGJsb2NrKTsKICB9OwogIGNvbnN0"
    "IG9wcFBlbiA9IHMucGVuYWx0aWVzID8gcy5wZW5hbHRpZXNbcy5vcHBfaWR4XSA6IG51bGw7CiAg"
    "Y29uc3QgbXlQZW4gID0gcy5wZW5hbHRpZXMgPyBzLnBlbmFsdGllc1tteVBsYXllcl0gIDogbnVs"
    "bDsKICBhZGRVbm0ocy51bm1hdGNoZWRfbWluZSwgJ1lvdXInLCBteVBlbiB8fCAwKTsKICBhZGRV"
    "bm0ocy51bm1hdGNoZWRfb3BwLCBzLm5hbWVzW3Mub3BwX2lkeF0sIG9wcFBlbiB8fCAwKTsKCiAg"
    "Ly8gTmV4dCBidXR0b24gbGFiZWwKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYnRuLW5leHQn"
    "KS50ZXh0Q29udGVudCA9CiAgICBpc01hdGNoT3ZlciA/ICfwn46uIE5ldyBNYXRjaCcgOiAn4pa2"
    "IE5leHQgSGFuZCc7Cn0KCmZ1bmN0aW9uIGNsb3NlUmVzdWx0KCkgewogIGRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCdyZXN1bHQtb3ZlcmxheScpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7Cn0KCi8v"
    "IOKUgOKUgCBVSSBoZWxwZXJzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzaG93RGNCYW5uZXIoc2hv"
    "dywgbXNnKSB7CiAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZGMtYmFubmVy"
    "Jyk7CiAgZWwuc3R5bGUuZGlzcGxheSA9IHNob3cgPyAnJyA6ICdub25lJzsKICBpZiAobXNnKSBl"
    "bC50ZXh0Q29udGVudCA9IG1zZzsKfQoKLy8g4pSA4pSAIEhlYXJ0YmVhdCDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIAKc2V0SW50ZXJ2YWwoKCkgPT4geyBpZiAod3MgJiYgd3MucmVhZHlTdGF0ZSA9PT0gV2ViU29j"
    "a2V0Lk9QRU4pIHNlbmQoe2FjdGlvbjoncGluZyd9KTsgfSwgMjAwMDApOwoKLy8g4pSA4pSAIEtl"
    "eWJvYXJkIHNob3J0Y3V0cyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIAKZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigna2V5ZG93bicsIGUgPT4gewogIGlmICgh"
    "c3RhdGUgfHwgIXN0YXRlLm15X3R1cm4pIHJldHVybjsKICAvLyBFbnRlciBpbiBsb2JieSBuYW1l"
    "IGZpZWxkIOKGkiBjcmVhdGUgcm9vbQogIGNvbnN0IGZvY3VzZWQgPSBkb2N1bWVudC5hY3RpdmVF"
    "bGVtZW50OwogIGlmIChmb2N1c2VkICYmIGZvY3VzZWQuaWQgPT09ICdpbnAtbmFtZScgJiYgZS5r"
    "ZXkgPT09ICdFbnRlcicpIGNyZWF0ZVJvb20oKTsKICBpZiAoZm9jdXNlZCAmJiBmb2N1c2VkLmlk"
    "ID09PSAnaW5wLXJvb20nICYmIGUua2V5ID09PSAnRW50ZXInKSBqb2luUm9vbSgpOwp9KTsKPC9z"
    "Y3JpcHQ+CjwvYm9keT4KPC9odG1sPgo="
)
_INDEX_HTML = base64.b64decode(_HTML_B64).decode("utf-8")

# ── App ───────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)
app = FastAPI(title="Chinchon Multiplayer")

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return HTMLResponse(_INDEX_HTML)

# ── Room management ───────────────────────────────────────────────────────────
class Room:
    def __init__(self, code):
        self.code    = code
        self.sockets = []
        self.names   = []
        self.game    = None
        self.created = time.time()

    @property
    def n_players(self): return len(self.sockets)
    def player_idx(self, ws): return self.sockets.index(ws)

rooms = {}

def _gen_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in rooms:
            return code

def _cleanup():
    cutoff = time.time() - 7200
    stale  = [k for k, r in rooms.items() if r.created < cutoff and r.n_players == 0]
    for k in stale: del rooms[k]

async def _broadcast(room):
    if room.game is None: return
    for idx, ws in enumerate(room.sockets):
        payload = player_view(room.game, idx)
        payload["type"] = "state"
        try: await ws.send_json(payload)
        except Exception: pass

async def _send(ws, msg):
    try: await ws.send_json(msg)
    except Exception: pass

async def _send_lobby(room):
    for ws in room.sockets:
        await _send(ws, {"type":"lobby","room":room.code,
                         "names":room.names,"waiting":room.n_players<2})

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    room   = None
    player = -1
    try:
        raw    = await asyncio.wait_for(websocket.receive_text(), timeout=30)
        msg    = json.loads(raw)
        name   = (msg.get("name") or "Player").strip()[:20] or "Player"
        action = msg.get("action","")

        if action == "create":
            _cleanup()
            code = _gen_code()
            room = Room(code)
            rooms[code] = room
            room.sockets.append(websocket)
            room.names.append(name)
            player = 0
            log.info("Room %s created by %s", code, name)
            await _send(websocket, {"type":"created","room":code,"player":0})
            await _send_lobby(room)

        elif action == "join":
            code = (msg.get("room") or "").upper().strip()
            if code not in rooms:
                await _send(websocket, {"type":"error","msg":f"Room {code} not found."})
                await websocket.close(); return
            room = rooms[code]
            if room.n_players >= 2:
                await _send(websocket, {"type":"error","msg":"Room is full."})
                await websocket.close(); return
            room.sockets.append(websocket)
            room.names.append(name)
            player = 1
            log.info("Room %s: %s joined", code, name)
            room.game = new_game(room.names)
            await _send(websocket, {"type":"joined","room":code,"player":1})
            await _broadcast(room)

        else:
            await _send(websocket, {"type":"error","msg":"First message must be create or join."})
            await websocket.close(); return

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            act = msg.get("action","")
            if room.game is None:
                await _send(websocket, {"type":"error","msg":"Game not started."}); continue
            ok, err = True, ""
            if   act == "draw_deck":    ok,err = action_draw_deck(room.game, player)
            elif act == "draw_discard": ok,err = action_draw_discard(room.game, player)
            elif act == "discard":      ok,err = action_discard(room.game, player, int(msg.get("idx",-1)))
            elif act == "declare":      ok,err = action_declare(room.game, player)
            elif act == "move":         ok,err = action_move(room.game, player, int(msg.get("i",0)), int(msg.get("j",0)))
            elif act == "next_hand":
                if room.game["phase"] in ("hand_over","match_over"):
                    room.game = new_game(room.names) if room.game["phase"]=="match_over" else new_hand(room.game)
            elif act == "reset": room.game = new_game(room.names)
            elif act == "ping":  await _send(websocket,{"type":"pong"}); continue
            else: await _send(websocket,{"type":"error","msg":f"Unknown: {act}"}); continue
            if not ok: await _send(websocket,{"type":"error","msg":err})
            else:      await _broadcast(room)

    except WebSocketDisconnect:
        log.info("Player %d disconnected from room %s", player, room.code if room else "?")
    except asyncio.TimeoutError:
        log.info("Timeout on first message")
    except Exception as e:
        log.exception("Error: %s", e)
    finally:
        if room and websocket in room.sockets:
            room.sockets.remove(websocket)
            if player < len(room.names):
                gone = room.names[player]
                for ws in room.sockets:
                    await _send(ws,{"type":"player_left","msg":f"{gone} disconnected."})
            if room.n_players == 0:
                rooms.pop(room.code, None)
                log.info("Room %s closed", room.code)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
