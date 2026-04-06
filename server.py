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
    """Swap cards — allowed any time the game is active, not just your turn."""
    if state['phase'] not in ('draw', 'discard'):
        return False, "Cannot reorder now."
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
    "YW5zZm9ybTogdHJhbnNsYXRlWSgtNnB4KTsgY3Vyc29yOiBwb2ludGVyOyB9Ci5jYXJkLXN2Zy1v"
    "dXRlcltkcmFnZ2FibGU9dHJ1ZV0geyBjdXJzb3I6IGdyYWI7IH0KLmNhcmQtc3ZnLW91dGVyW2Ry"
    "YWdnYWJsZT10cnVlXTphY3RpdmUgeyBjdXJzb3I6IGdyYWJiaW5nOyB9Ci5jYXJkLXN2Zy1vdXRl"
    "ci5kcmFnZ2luZyAgeyBvcGFjaXR5OiAwLjI1OyB0cmFuc2Zvcm06IHNjYWxlKDAuOTIpICFpbXBv"
    "cnRhbnQ7IHRyYW5zaXRpb246IG5vbmU7IH0KLmNhcmQtc3ZnLW91dGVyLmRyYWctb3ZlciB7IHRy"
    "YW5zZm9ybTogdHJhbnNsYXRlWSgtMTJweCkgc2NhbGUoMS4wNSkgIWltcG9ydGFudDsKICAgIGJv"
    "cmRlcjogM3B4IHNvbGlkICM2MGE1ZmEgIWltcG9ydGFudDsKICAgIGJveC1zaGFkb3c6IDAgMCAy"
    "MHB4IHJnYmEoOTYsMTY1LDI1MCwwLjU1KSwgMnB4IDVweCAxMHB4IHJnYmEoMCwwLDAsMC40KSAh"
    "aW1wb3J0YW50OyB9Ci5jYXJkLWJhY2sgewogIHdpZHRoOiA3MnB4OyBoZWlnaHQ6IDEwOHB4OyBi"
    "b3JkZXItcmFkaXVzOiA3cHg7CiAgYmFja2dyb3VuZDogbGluZWFyLWdyYWRpZW50KDE2MGRlZywj"
    "MWU0MGFmLCMyNTYzZWIgNjAlLCMxZTNhOGEpOwogIGJvcmRlcjogMnB4IHNvbGlkICM2MGE1ZmE7"
    "IGJveC1zaGFkb3c6IDJweCA1cHggMTBweCByZ2JhKDAsMCwwLC41KTsKICBkaXNwbGF5OiBpbmxp"
    "bmUtZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVzdGlmeS1jb250ZW50OiBjZW50ZXI7CiAg"
    "ZmxleC1zaHJpbms6IDA7IGZvbnQtc2l6ZTogMS42cmVtOyBjb2xvcjogcmdiYSgyNTUsMjU1LDI1"
    "NSwuMik7Cn0KLmNhcmQtY29ybmVyIHsKICBwb3NpdGlvbjogYWJzb2x1dGU7IGZvbnQtc2l6ZTog"
    "MTNweDsgZm9udC13ZWlnaHQ6IDkwMDsgbGluZS1oZWlnaHQ6IDE7CiAgdGV4dC1zaGFkb3c6IDAg"
    "MCA0cHggI2ZmZjgsIDAgMXB4IDJweCAjZmZmOwp9Ci5jYXJkLWNvcm5lci50bCB7IHRvcDogM3B4"
    "OyBsZWZ0OiA0cHg7IH0KLmNhcmQtY29ybmVyLmJyIHsgYm90dG9tOiAzcHg7IHJpZ2h0OiA0cHg7"
    "IHRyYW5zZm9ybTogcm90YXRlKDE4MGRlZyk7IH0KCi8qIFN3YXAgLyBkaXNjYXJkIGJ1dHRvbnMg"
    "YmVsb3cgY2FyZHMgKi8KLmNhcmQtYnRuLWNvbCB7IHdpZHRoOiA3MnB4OyBkaXNwbGF5OiBmbGV4"
    "OyBmbGV4LWRpcmVjdGlvbjogY29sdW1uOyBnYXA6IDJweDsgfQouY2FyZC1idG4tY29sIC5zd2Fw"
    "LXBhaXIgeyBkaXNwbGF5OiBmbGV4OyBnYXA6IDJweDsgfQoubWluaS1idG4gewogIGZsZXg6IDE7"
    "IHBhZGRpbmc6IDNweCAwOyBib3JkZXItcmFkaXVzOiA1cHg7IGJvcmRlcjogbm9uZTsgY3Vyc29y"
    "OiBwb2ludGVyOwogIGZvbnQtc2l6ZTogLjc1cmVtOyBmb250LXdlaWdodDogNzAwOwogIGJhY2tn"
    "cm91bmQ6IHJnYmEoMjU1LDI1NSwyNTUsLjEyKTsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjcp"
    "OwogIHRyYW5zaXRpb246IGJhY2tncm91bmQgLjEyczsKfQoubWluaS1idG46aG92ZXI6bm90KDpk"
    "aXNhYmxlZCkgeyBiYWNrZ3JvdW5kOiByZ2JhKDI1NSwyNTUsMjU1LC4yNSk7IH0KLm1pbmktYnRu"
    "OmRpc2FibGVkIHsgb3BhY2l0eTogLjI1OyBjdXJzb3I6IGRlZmF1bHQ7IH0KLmRpc2MtYnRuIHsK"
    "ICB3aWR0aDogNzJweDsgcGFkZGluZzogNHB4IDA7IGJvcmRlci1yYWRpdXM6IDVweDsgYm9yZGVy"
    "OiBub25lOyBjdXJzb3I6IHBvaW50ZXI7CiAgZm9udC1zaXplOiAuNzVyZW07IGZvbnQtd2VpZ2h0"
    "OiA3MDA7IHRyYW5zaXRpb246IGFsbCAuMTJzOwogIGJhY2tncm91bmQ6IHJnYmEoMCwwLDAsLjM1"
    "KTsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjcpOwp9Ci5kaXNjLWJ0bi5kLXN0b3AgICAgeyBi"
    "YWNrZ3JvdW5kOiAjMTZhMzRhOyBjb2xvcjogI2ZmZjsgfQouZGlzYy1idG4uZC1jaGluY2hvbiB7"
    "IGJhY2tncm91bmQ6ICNkOTc3MDY7IGNvbG9yOiAjZmZmOyBmb250LXNpemU6IC43cmVtOyB9Ci5k"
    "aXNjLWJ0bjpob3Zlcjpub3QoOmRpc2FibGVkKSB7IGZpbHRlcjogYnJpZ2h0bmVzcygxLjE1KTsg"
    "fQouZGlzYy1idG46ZGlzYWJsZWQgeyBvcGFjaXR5OiAuMzsgY3Vyc29yOiBkZWZhdWx0OyB9Cgov"
    "KiBNZWxkIGRpc3BsYXkgKi8KLm1lbGQtYmxvY2sgeyBtYXJnaW4tdG9wOiA4cHg7IHRleHQtYWxp"
    "Z246IGxlZnQ7IH0KLm1lbGQtbGFiZWwgeyBmb250LXNpemU6IC43cmVtOyBjb2xvcjogcmdiYSgy"
    "NTUsMjU1LDI1NSwuNDUpOyBtYXJnaW4tYm90dG9tOiA0cHg7IH0KLm1lbGQtcm93IHsgZGlzcGxh"
    "eTogZmxleDsgZ2FwOiA0cHg7IGZsZXgtd3JhcDogd3JhcDsgbWFyZ2luLWJvdHRvbTogNnB4OyB9"
    "CgovKiBQZW5hbHR5IHJvdyAqLwoucGVuYWx0eS1yb3cgeyBkaXNwbGF5OiBmbGV4OyBnYXA6IDIw"
    "cHg7IGp1c3RpZnktY29udGVudDogY2VudGVyOyBtYXJnaW46IDEwcHggMDsgfQoucGVuLWJveCB7"
    "IHRleHQtYWxpZ246IGNlbnRlcjsgfQoucGVuLW5hbWUgeyBmb250LXNpemU6IC43cmVtOyBjb2xv"
    "cjogcmdiYSgyNTUsMjU1LDI1NSwuNCk7IHRleHQtdHJhbnNmb3JtOiB1cHBlcmNhc2U7IGxldHRl"
    "ci1zcGFjaW5nOiAxcHg7IH0KLnBlbi12YWwgIHsgZm9udC1zaXplOiAxLjJyZW07IGZvbnQtd2Vp"
    "Z2h0OiA4MDA7IH0KCi8qIFdhaXRpbmcgc3Bpbm5lciAqLwoud2FpdGluZyB7IHRleHQtYWxpZ246"
    "IGNlbnRlcjsgcGFkZGluZzogMjBweDsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsLjUpOyB9Ci5k"
    "b3Qtc3BpbiB7IGRpc3BsYXk6IGlubGluZS1ibG9jazsgYW5pbWF0aW9uOiBzcGluIDFzIGxpbmVh"
    "ciBpbmZpbml0ZTsgfQpAa2V5ZnJhbWVzIHNwaW4geyB0byB7IHRyYW5zZm9ybTogcm90YXRlKDM2"
    "MGRlZyk7IH0gfQoKLyogTGVnZW5kICovCiNsZWdlbmQgewogIHRleHQtYWxpZ246IGNlbnRlcjsg"
    "Zm9udC1zaXplOiAuNjVyZW07IGNvbG9yOiByZ2JhKDI1NSwyNTUsMjU1LC4yNSk7CiAgcGFkZGlu"
    "ZzogNnB4IDEwcHggMTBweDsKfQoKLyogRGlzY29ubmVjdGlvbiBiYW5uZXIgKi8KI2RjLWJhbm5l"
    "ciB7CiAgZGlzcGxheTogbm9uZTsgcG9zaXRpb246IGZpeGVkOyB0b3A6IDA7IGxlZnQ6IDA7IHJp"
    "Z2h0OiAwOwogIGJhY2tncm91bmQ6ICM3ZjFkMWQ7IGNvbG9yOiAjZmNhNWE1OyB0ZXh0LWFsaWdu"
    "OiBjZW50ZXI7CiAgcGFkZGluZzogOHB4IDE0cHg7IGZvbnQtc2l6ZTogLjg1cmVtOyB6LWluZGV4"
    "OiAyMDA7Cn0KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KCjwhLS0g4pWQ4pWQIExPQkJZIOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KPGRpdiBpZD0ibG9iYnkiPgogIDxoMT7wn4OPIENo"
    "aW5jaMOzbjwvaDE+CiAgPHA+QmFyYWphIEVzcGHDsW9sYSDCtyA0MCBjYXJkcyDCtyAyIHBsYXll"
    "cnM8L3A+CgogIDxkaXYgY2xhc3M9ImxvYmJ5LWNhcmQiPgogICAgPGgyPvCfkaQgWW91ciBuYW1l"
    "PC9oMj4KICAgIDxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0iaW5wLW5hbWUiIHBsYWNlaG9sZGVyPSJF"
    "bnRlciB5b3VyIG5hbWXigKYiIG1heGxlbmd0aD0iMjAiPgogIDwvZGl2PgoKICA8ZGl2IGNsYXNz"
    "PSJsb2JieS1jYXJkIj4KICAgIDxoMj7wn4aVIENyZWF0ZSBhIHJvb208L2gyPgogICAgPHA+U2hh"
    "cmUgdGhlIDQtbGV0dGVyIGNvZGUgd2l0aCB5b3VyIG9wcG9uZW50LjwvcD4KICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBidG4tZ3JlZW4iIG9uY2xpY2s9ImNyZWF0ZVJvb20oKSI+Q3JlYXRlIFJvb208"
    "L2J1dHRvbj4KICA8L2Rpdj4KCiAgPGRpdiBjbGFzcz0ibG9iYnktY2FyZCI+CiAgICA8aDI+8J+U"
    "lyBKb2luIGEgcm9vbTwvaDI+CiAgICA8aW5wdXQgdHlwZT0idGV4dCIgaWQ9ImlucC1yb29tIiBw"
    "bGFjZWhvbGRlcj0iUm9vbSBjb2RlIChlLmcuIEFCQ0QpIiBtYXhsZW5ndGg9IjQiCiAgICAgICAg"
    "ICAgc3R5bGU9InRleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzozcHg7Ij4K"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tYmx1ZSIgb25jbGljaz0iam9pblJvb20oKSI+Sm9p"
    "biBSb29tPC9idXR0b24+CiAgPC9kaXY+CgogIDxkaXYgaWQ9ImxvYmJ5LW1zZyIgc3R5bGU9ImNv"
    "bG9yOiNmY2E1YTU7Zm9udC1zaXplOi44NXJlbTtkaXNwbGF5Om5vbmU7Ij48L2Rpdj4KPC9kaXY+"
    "Cgo8IS0tIOKVkOKVkCBHQU1FIEJPQVJEIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KPGRpdiBpZD0iZ2Ft"
    "ZSI+CiAgPGRpdiBpZD0iZGMtYmFubmVyIj7imqAgT3Bwb25lbnQgZGlzY29ubmVjdGVkIOKAlCB3"
    "YWl0aW5nIGZvciB0aGVtIHRvIHJlam9pbuKApjwvZGl2PgoKICA8IS0tIEhlYWRlciAtLT4KICA8"
    "ZGl2IGlkPSJoZWFkZXIiPgogICAgPGRpdiBjbGFzcz0icm9vbS1jb2RlIj5Sb29tIDxzcGFuIGlk"
    "PSJoZHItcm9vbSI+4oCUPC9zcGFuPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ic2NvcmUtYm94Ij4K"
    "ICAgICAgPGRpdiBjbGFzcz0ic25hbWUiIGlkPSJzY29yZTAtbmFtZSI+4oCUPC9kaXY+CiAgICAg"
    "IDxkaXYgY2xhc3M9InN2YWwiICBpZD0ic2NvcmUwLXZhbCI+MDwvZGl2PgogICAgPC9kaXY+CiAg"
    "ICA8ZGl2IGNsYXNzPSJzY29yZS1ib3giPgogICAgICA8ZGl2IGNsYXNzPSJzbmFtZSIgaWQ9InNj"
    "b3JlMS1uYW1lIj7igJQ8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3ZhbCIgIGlkPSJzY29yZTEt"
    "dmFsIj4wPC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InNwYWNlciI+PC9kaXY+CiAg"
    "ICA8ZGl2IGlkPSJ0dXJuLWJhbm5lciIgY2xhc3M9InR1cm4tb3BwIj5XYWl0aW5n4oCmPC9kaXY+"
    "CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLWdvbGQgYnRuLXNtIiBpZD0iYnRuLWRlY2xhcmUi"
    "IG9uY2xpY2s9InNlbmREZWNsYXJlKCkiIHN0eWxlPSJkaXNwbGF5Om5vbmUiPuKciyBEZWNsYXJl"
    "IFdpbjwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1vcmFuZ2UgYnRuLXNtIiBv"
    "bmNsaWNrPSJzZW5kUmVzZXQoKSI+8J+UhCBSZXNldDwvYnV0dG9uPgogIDwvZGl2PgoKICA8IS0t"
    "IEJvYXJkIC0tPgogIDxkaXYgaWQ9ImJvYXJkIj4KICAgIDwhLS0gT3Bwb25lbnQgaGFuZCAtLT4K"
    "ICAgIDxkaXYgaWQ9Im9wcC1hcmVhIj4KICAgICAgPGRpdiBpZD0ib3BwLW5hbWUtcm93Ij4KICAg"
    "ICAgICA8ZGl2IGNsYXNzPSJzZWMtbGJsIiBpZD0ib3BwLWxhYmVsIj5PcHBvbmVudDwvZGl2Pgog"
    "ICAgICA8L2Rpdj4KICAgICAgPGRpdiBpZD0ib3BwLWhhbmQiPjwvZGl2PgogICAgPC9kaXY+Cgog"
    "ICAgPCEtLSBUYWJsZTogZGVjayArIGRpc2NhcmQgKyBtZXNzYWdlIC0tPgogICAgPGRpdiBpZD0i"
    "dGFibGUtYXJlYSI+CiAgICAgIDxkaXYgY2xhc3M9InBpbGUiPgogICAgICAgIDxkaXYgY2xhc3M9"
    "InBpbGUtbGFiZWwiPvCfk6YgRGVjazwvZGl2PgogICAgICAgIDxkaXYgaWQ9ImRlY2stY2FyZCIg"
    "Y2xhc3M9ImNhcmQtYmFjayBjbGlja2FibGUiIG9uY2xpY2s9ImRyYXdEZWNrKCkiPuKcpjwvZGl2"
    "PgogICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZTouNjVyZW07Y29sb3I6cmdiYSgyNTUsMjU1"
    "LDI1NSwuMzUpO21hcmdpbi10b3A6MnB4OyIgaWQ9ImRlY2stY291bnQiPjwvZGl2PgogICAgICA8"
    "L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0icGlsZSI+CiAgICAgICAgPGRpdiBjbGFzcz0icGlsZS1s"
    "YWJlbCI+8J+XgyBEaXNjYXJkPC9kaXY+CiAgICAgICAgPGRpdiBpZD0iZGlzY2FyZC1jYXJkIj48"
    "L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgaWQ9Im1zZy1ib3giPkNvbm5lY3RpbmfigKY8"
    "L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0gUGxheWVyIGhhbmQgbGFiZWwgLS0+CiAgICA8ZGl2"
    "IGlkPSJteS1hcmVhIj4KICAgICAgPGRpdiBjbGFzcz0ic2VjLWxibCI+8J+nkSBZb3VyIGhhbmQg"
    "Jm5ic3A7wrcmbmJzcDsgPHNwYW4gc3R5bGU9Im9wYWNpdHk6LjU7Zm9udC1zaXplOi42cmVtOyI+"
    "ZHJhZyBjYXJkcyB0byByZW9yZGVyPC9zcGFuPjwvZGl2PgogICAgICA8IS0tIENhcmRzIC0tPgog"
    "ICAgICA8ZGl2IGlkPSJteS1oYW5kLXJvdyI+PC9kaXY+CiAgICAgIDwhLS0gU3dhcCB2aWEgZHJh"
    "Zy1hbmQtZHJvcCAtLT4KICAgICAgPCEtLSBEaXNjYXJkIGJ1dHRvbnMgLS0+CiAgICAgIDxkaXYg"
    "aWQ9ImRpc2NhcmQtcm93Ij48L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0gTGVnZW5kIC0tPgog"
    "ICAgPGRpdiBpZD0ibGVnZW5kIj4KICAgICAg8J+qmSBPcm9zICZuYnNwO8K3Jm5ic3A7IPCfjbcg"
    "Q29wYXMgJm5ic3A7wrcmbmJzcDsg4pqU77iPIEVzcGFkYXMgJm5ic3A7wrcmbmJzcDsg8J+PkSBC"
    "YXN0b3MKICAgICAgJm5ic3A7Jm5ic3A7fCZuYnNwOyZuYnNwOwogICAgICBBIDIgMyA0IDUgNiA3"
    "IDEwIDExIDEyCiAgICAgICZuYnNwOyZuYnNwO3wmbmJzcDsmbmJzcDsKICAgICAgQmx1ZSBnbG93"
    "ID0ganVzdCBkcmF3biAmbmJzcDvCtyZuYnNwOyBHb2xkIGdsb3cgPSBDaGluY2jDs24gJm5ic3A7"
    "wrcmbmJzcDsgR3JlZW4gZ2xvdyA9IFN0b3AKICAgIDwvZGl2PgogIDwvZGl2Pgo8L2Rpdj4KCjwh"
    "LS0g4pWQ4pWQIFJFU1VMVCBPVkVSTEFZIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KPGRpdiBpZD0icmVzdWx0LW92ZXJsYXki"
    "PgogIDxkaXYgaWQ9InJlc3VsdC1jYXJkIj4KICAgIDxkaXYgaWQ9InJlcy1lbW9qaSIgc3R5bGU9"
    "ImZvbnQtc2l6ZToyLjVyZW07bWFyZ2luLWJvdHRvbTo2cHg7Ij7wn46JPC9kaXY+CiAgICA8aDIg"
    "aWQ9InJlcy10aXRsZSI+4oCUPC9oMj4KICAgIDxkaXYgaWQ9InJlcy1wZW5hbHRpZXMiIGNsYXNz"
    "PSJwZW5hbHR5LXJvdyIgc3R5bGU9Im1hcmdpbjoxMnB4IDA7Ij48L2Rpdj4KICAgIDxkaXYgaWQ9"
    "InJlcy1tZWxkcyI+PC9kaXY+CiAgICA8ZGl2IGlkPSJyZXMtdW5tYXRjaGVkIj48L2Rpdj4KICAg"
    "IDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MTBweDtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVy"
    "O21hcmdpbi10b3A6MThweDsiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLWdyZWVuIiBp"
    "ZD0iYnRuLW5leHQiIG9uY2xpY2s9InNlbmROZXh0SGFuZCgpIj7ilrYgTmV4dCBIYW5kPC9idXR0"
    "b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tb3JhbmdlIiBvbmNsaWNrPSJzZW5kUmVz"
    "ZXQoKSI+8J+UhCBOZXcgR2FtZTwvYnV0dG9uPgogICAgPC9kaXY+CiAgPC9kaXY+CjwvZGl2PgoK"
    "PHNjcmlwdD4KLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQCi8vICBTVkcgQ0FSRCBSRU5ERVJJTkcKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCmNvbnN0IENXID0g"
    "NzIsIENIID0gMTA4OwoKY29uc3QgUE9TID0gewogIDE6IFtbMzYsNTRdXSwKICAyOiBbWzM2LDMw"
    "XSxbMzYsNzhdXSwKICAzOiBbWzM2LDI0XSxbMzYsNTRdLFszNiw4NF1dLAogIDQ6IFtbMjIsMzJd"
    "LFs1MCwzMl0sWzIyLDc2XSxbNTAsNzZdXSwKICA1OiBbWzIyLDI0XSxbNTAsMjRdLFszNiw1NF0s"
    "WzIyLDg0XSxbNTAsODRdXSwKICA2OiBbWzIyLDI0XSxbNTAsMjRdLFsyMiw1NF0sWzUwLDU0XSxb"
    "MjIsODRdLFs1MCw4NF1dLAogIDc6IFtbMjIsMjBdLFs1MCwyMF0sWzM2LDM4XSxbMjIsNTRdLFs1"
    "MCw1NF0sWzIyLDc2XSxbNTAsNzZdXSwKfTsKY29uc3QgU1pTID0gezE6MTUsMjoxMywzOjEyLDQ6"
    "MTAsNTo5LDY6OSw3Ojh9OwoKZnVuY3Rpb24gZihuKSB7IHJldHVybiAoK24pLnRvRml4ZWQoMSk7"
    "IH0KCmZ1bmN0aW9uIHN5bVN2ZyhzdWl0LCBjeCwgY3ksIHN6KSB7CiAgaWYgKHN1aXQgPT09ICdP"
    "cm9zJykgewogICAgY29uc3Qgc3cgPSBNYXRoLm1heCgxLjEsIHN6KjAuMTIpOwogICAgcmV0dXJu"
    "IGA8Y2lyY2xlIGN4PSIke2YoY3gpfSIgY3k9IiR7ZihjeSl9IiByPSIke2Yoc3opfSIgZmlsbD0i"
    "I0Y2QzgyMCIgc3Ryb2tlPSIjQTA3ODE4IiBzdHJva2Utd2lkdGg9IiR7Zihzdyl9Ii8+CiAgICAg"
    "ICAgICAgIDxjaXJjbGUgY3g9IiR7ZihjeCl9IiBjeT0iJHtmKGN5KX0iIHI9IiR7ZihzeiowLjYy"
    "KX0iIGZpbGw9Im5vbmUiIHN0cm9rZT0iI0EwNzgxOCIgc3Ryb2tlLXdpZHRoPSIke2Yoc3cqMC41"
    "NSl9Ii8+CiAgICAgICAgICAgIDxjaXJjbGUgY3g9IiR7ZihjeCl9IiBjeT0iJHtmKGN5KX0iIHI9"
    "IiR7ZihzeiowLjI2KX0iIGZpbGw9IiNBMDc4MTgiLz5gOwogIH0KICBpZiAoc3VpdCA9PT0gJ0Vz"
    "cGFkYXMnKSB7CiAgICBjb25zdCBidyA9IE1hdGgubWF4KDEuNCwgc3oqMC4xNyksIGd3ID0gc3oq"
    "MC43MjsKICAgIHJldHVybiBgPHBvbHlnb24gcG9pbnRzPSIke2YoY3gpfSwke2YoY3ktc3opfSAk"
    "e2YoY3gtYncpfSwke2YoY3ktc3oqMC4yOCl9ICR7ZihjeCtidyl9LCR7ZihjeS1zeiowLjI4KX0i"
    "IGZpbGw9IiM0MDY4QjgiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtmKGN4LWJ3KX0iIHk9IiR7"
    "ZihjeS1zeiowLjI4KX0iIHdpZHRoPSIke2YoYncqMil9IiBoZWlnaHQ9IiR7ZihzeiowLjU0KX0i"
    "IGZpbGw9IiM0MDY4QjgiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtmKGN4LWd3KX0iIHk9IiR7"
    "ZihjeStzeiowLjI0KX0iIHdpZHRoPSIke2YoZ3cqMil9IiBoZWlnaHQ9IiR7ZihzeiowLjE3KX0i"
    "IGZpbGw9IiM5QjZFMjIiIHJ4PSIyIi8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7ZihjeC1idyox"
    "LjYpfSIgeT0iJHtmKGN5K3N6KjAuNDEpfSIgd2lkdGg9IiR7ZihidyozLjIpfSIgaGVpZ2h0PSIk"
    "e2Yoc3oqMC41OSl9IiBmaWxsPSIjN0E0QzE4IiByeD0iMiIvPmA7CiAgfQogIGlmIChzdWl0ID09"
    "PSAnQ29wYXMnKSB7CiAgICBjb25zdCBidz1zeiowLjg2LCBiaD1zeiowLjcwLCB5MD1jeS1zeiwg"
    "eW09eTArYmg7CiAgICBjb25zdCBzdzI9c3oqMC4xNSwgc2g9c3oqMC40MiwgYmF3PXN6KjAuNTg7"
    "CiAgICByZXR1cm4gYDxwYXRoIGQ9Ik0ke2YoY3gtYncpfSwke2YoeTApfSBRJHtmKGN4LWJ3KX0s"
    "JHtmKHltKX0gJHtmKGN4KX0sJHtmKHltKX0gUSR7ZihjeCtidyl9LCR7Zih5bSl9ICR7ZihjeCti"
    "dyl9LCR7Zih5MCl9IFoiIGZpbGw9IiNDQzE4MDAiIHN0cm9rZT0iIzg4MDAwMCIgc3Ryb2tlLXdp"
    "ZHRoPSIwLjkiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtmKGN4LXN3Mil9IiB5PSIke2YoeW0p"
    "fSIgd2lkdGg9IiR7ZihzdzIqMil9IiBoZWlnaHQ9IiR7ZihzaCl9IiBmaWxsPSIjQUExNDAwIi8+"
    "CiAgICAgICAgICAgIDxlbGxpcHNlIGN4PSIke2YoY3gpfSIgY3k9IiR7Zih5bStzaCl9IiByeD0i"
    "JHtmKGJhdyl9IiByeT0iJHtmKHN6KjAuMTcpfSIgZmlsbD0iIzg4MDAwMCIvPmA7CiAgfQogIC8v"
    "IEJhc3RvcwogIGNvbnN0IGFuZz0yMCpNYXRoLlBJLzE4MCwgcnhfPU1hdGgubWF4KDMuNSxzeiow"
    "LjMwKSwga3I9TWF0aC5tYXgoNC4wLHN6KjAuNDApOwogIGNvbnN0IHR4PWN4LU1hdGguc2luKGFu"
    "Zykqc3osIHR5PWN5LU1hdGguY29zKGFuZykqc3o7CiAgY29uc3QgYng9Y3grTWF0aC5zaW4oYW5n"
    "KSpzeiwgYnlfPWN5K01hdGguY29zKGFuZykqc3o7CiAgcmV0dXJuIGA8ZWxsaXBzZSBjeD0iJHtm"
    "KGN4KX0iIGN5PSIke2YoY3kpfSIgcng9IiR7ZihyeF8pfSIgcnk9IiR7Zihzeil9IiBmaWxsPSIj"
    "NTg5MDMwIiBzdHJva2U9IiMyODYwMTAiIHN0cm9rZS13aWR0aD0iMC44IiB0cmFuc2Zvcm09InJv"
    "dGF0ZSgyMCwke2YoY3gpfSwke2YoY3kpfSkiLz4KICAgICAgICAgIDxjaXJjbGUgY3g9IiR7Zih0"
    "eCl9IiBjeT0iJHtmKHR5KX0iIHI9IiR7Zihrcil9IiBmaWxsPSIjNDg3ODIwIi8+CiAgICAgICAg"
    "ICA8Y2lyY2xlIGN4PSIke2YoYngpfSIgY3k9IiR7ZihieV8pfSIgcj0iJHtmKGtyKX0iIGZpbGw9"
    "IiM0ODc4MjAiLz5gOwp9CgpmdW5jdGlvbiBmYWNlQm9keVN2ZyhzdWl0LCB2KSB7CiAgY29uc3Qg"
    "Y3g9Q1cvMiwgY3k9Q0gvMjsKICBjb25zdCBiYW5kPXtPcm9zOicjQzg5MDEwJyxFc3BhZGFzOicj"
    "MjA1MEEwJyxDb3BhczonI0FBMTQwMCcsQmFzdG9zOicjMkU2ODE4J31bc3VpdF07CiAgbGV0IG91"
    "dCA9IGA8cmVjdCB4PSIwIiB5PSIwIiB3aWR0aD0iJHtDV30iIGhlaWdodD0iMjgiIGZpbGw9IiR7"
    "YmFuZH0iIG9wYWNpdHk9IjAuMzUiIHJ4PSI1Ii8+CiAgICAgICAgICAgICA8cmVjdCB4PSIwIiB5"
    "PSIke0NILTI4fSIgd2lkdGg9IiR7Q1d9IiBoZWlnaHQ9IjI4IiBmaWxsPSIke2JhbmR9IiBvcGFj"
    "aXR5PSIwLjM1Ii8+YDsKICBpZiAodj09PTEwKSB7IC8vIFNvdGEKICAgIG91dCArPSBgPGNpcmNs"
    "ZSBjeD0iJHtjeH0iIGN5PSIke2N5LTI0fSIgcj0iMTAiIGZpbGw9IiNGREJGNzgiIHN0cm9rZT0i"
    "IzhCNUEyOCIgc3Ryb2tlLXdpZHRoPSIxIi8+CiAgICAgICAgICAgIDxyZWN0IHg9IiR7Y3gtMTF9"
    "IiB5PSIke2N5LTE0fSIgd2lkdGg9IjIyIiBoZWlnaHQ9IjMwIiByeD0iNCIgZmlsbD0iJHtiYW5k"
    "fSIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2N4LTh9IiB5PSIke2N5KzE2fSIgd2lkdGg9Ijci"
    "IGhlaWdodD0iMTgiIHJ4PSIzIiBmaWxsPSIke2JhbmR9Ii8+CiAgICAgICAgICAgIDxyZWN0IHg9"
    "IiR7Y3grMX0iIHk9IiR7Y3krMTZ9IiB3aWR0aD0iNyIgaGVpZ2h0PSIxOCIgcng9IjMiIGZpbGw9"
    "IiR7YmFuZH0iLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeC0xOH0iIHk9IiR7Y3ktMTF9IiB3"
    "aWR0aD0iOCIgaGVpZ2h0PSIxNCIgcng9IjMiIGZpbGw9IiR7YmFuZH0iLz4KICAgICAgICAgICAg"
    "PHJlY3QgeD0iJHtjeCsxMH0iIHk9IiR7Y3ktMTF9IiB3aWR0aD0iOCIgaGVpZ2h0PSIxNCIgcng9"
    "IjMiIGZpbGw9IiR7YmFuZH0iLz5gOwogIH0gZWxzZSBpZiAodj09PTExKSB7IC8vIENhYmFsbG8K"
    "ICAgIG91dCArPSBgPGVsbGlwc2UgY3g9IiR7Y3h9IiBjeT0iJHtjeSsxNn0iIHJ4PSIxOSIgcnk9"
    "IjEwIiBmaWxsPSIjRDBBMDcwIi8+CiAgICAgICAgICAgIDxjaXJjbGUgY3g9IiR7Y3grMTZ9IiBj"
    "eT0iJHtjeSs2fSIgcj0iNyIgZmlsbD0iI0QwQTA3MCIvPgogICAgICAgICAgICA8cmVjdCB4PSIk"
    "e2N4LTE1fSIgeT0iJHtjeSsyNH0iIHdpZHRoPSI1IiBoZWlnaHQ9IjE0IiByeD0iMiIgZmlsbD0i"
    "I0IwODA1MCIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2N4LTZ9IiB5PSIke2N5KzI0fSIgd2lk"
    "dGg9IjUiIGhlaWdodD0iMTQiIHJ4PSIyIiBmaWxsPSIjQjA4MDUwIi8+CiAgICAgICAgICAgIDxy"
    "ZWN0IHg9IiR7Y3grNH0iIHk9IiR7Y3krMjR9IiB3aWR0aD0iNSIgaGVpZ2h0PSIxNCIgcng9IjIi"
    "IGZpbGw9IiNCMDgwNTAiLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeCsxMn0iIHk9IiR7Y3kr"
    "MjR9IiB3aWR0aD0iNSIgaGVpZ2h0PSIxNCIgcng9IjIiIGZpbGw9IiNCMDgwNTAiLz4KICAgICAg"
    "ICAgICAgPGNpcmNsZSBjeD0iJHtjeC0yfSIgY3k9IiR7Y3ktMTZ9IiByPSI5IiBmaWxsPSIjRkRC"
    "Rjc4IiBzdHJva2U9IiM4QjVBMjgiIHN0cm9rZS13aWR0aD0iMSIvPgogICAgICAgICAgICA8cmVj"
    "dCB4PSIke2N4LTEwfSIgeT0iJHtjeS03fSIgd2lkdGg9IjE5IiBoZWlnaHQ9IjIyIiByeD0iMyIg"
    "ZmlsbD0iJHtiYW5kfSIvPmA7CiAgfSBlbHNlIHsgLy8gUmV5CiAgICBvdXQgKz0gYDxjaXJjbGUg"
    "Y3g9IiR7Y3h9IiBjeT0iJHtjeS0yMn0iIHI9IjEwIiBmaWxsPSIjRkRCRjc4IiBzdHJva2U9IiM4"
    "QjVBMjgiIHN0cm9rZS13aWR0aD0iMSIvPgogICAgICAgICAgICA8cG9seWdvbiBwb2ludHM9IiR7"
    "Y3gtMTB9LCR7Y3ktMzF9ICR7Y3gtMTB9LCR7Y3ktNDB9ICR7Y3gtNH0sJHtjeS0zM30gJHtjeH0s"
    "JHtjeS00Mn0gJHtjeCs0fSwke2N5LTMzfSAke2N4KzEwfSwke2N5LTQwfSAke2N4KzEwfSwke2N5"
    "LTMxfSIgZmlsbD0iI0Y4QzgyMCIgc3Ryb2tlPSIjQTA3MDEwIiBzdHJva2Utd2lkdGg9IjEuMiIv"
    "PgogICAgICAgICAgICA8Y2lyY2xlIGN4PSIke2N4fSIgY3k9IiR7Y3ktMzd9IiByPSIzLjUiIGZp"
    "bGw9IiNDQzIwMjAiLz4KICAgICAgICAgICAgPHBvbHlnb24gcG9pbnRzPSIke2N4LTE1fSwke2N5"
    "LTEyfSAke2N4KzE1fSwke2N5LTEyfSAke2N4KzE5fSwke2N5KzM2fSAke2N4LTE5fSwke2N5KzM2"
    "fSIgZmlsbD0iJHtiYW5kfSIvPgogICAgICAgICAgICA8cmVjdCB4PSIke2N4LTE0fSIgeT0iJHtj"
    "eSsyfSIgd2lkdGg9IjI4IiBoZWlnaHQ9IjYiIGZpbGw9IiNGOEM4MjAiLz4KICAgICAgICAgICAg"
    "PHJlY3QgeD0iJHtjeC0yMn0iIHk9IiR7Y3ktMTB9IiB3aWR0aD0iOSIgaGVpZ2h0PSIxNiIgcng9"
    "IjMiIGZpbGw9IiR7YmFuZH0iLz4KICAgICAgICAgICAgPHJlY3QgeD0iJHtjeCsxM30iIHk9IiR7"
    "Y3ktMTB9IiB3aWR0aD0iOSIgaGVpZ2h0PSIxNiIgcng9IjMiIGZpbGw9IiR7YmFuZH0iLz5gOwog"
    "IH0KICBvdXQgKz0gc3ltU3ZnKHN1aXQsIGN4LCBjeSszMCwgOSk7CiAgcmV0dXJuIG91dDsKfQoK"
    "ZnVuY3Rpb24gY2FyZFN2Z0lubmVyKGMpIHsKICBpZiAoYy52ID49IDEwKSByZXR1cm4gZmFjZUJv"
    "ZHlTdmcoYy5zLCBjLnYpOwogIHJldHVybiAoUE9TW2Mudl18fFtbMzYsNTRdXSkubWFwKChbcHgs"
    "cHldKSA9PiBzeW1TdmcoYy5zLCBweCwgcHksIFNaU1tjLnZdfHw5KSkuam9pbignJyk7Cn0KCmZ1"
    "bmN0aW9uIHJlbmRlckNhcmQoYywgb3B0cz17fSkgewogIGNvbnN0IGdsb3dDbGFzcyAgPSBvcHRz"
    "Lmdsb3dDbGFzcyAgfHwgJyc7CiAgY29uc3QgY2xpY2tDbHMgICA9IG9wdHMuY2xpY2thYmxlICA/"
    "ICcgY2xpY2thYmxlJyA6ICcnOwogIGNvbnN0IGlkQXR0ciAgICAgPSBvcHRzLmNhcmRJZCAgICAg"
    "PyBgIGlkPSIke29wdHMuY2FyZElkfSJgIDogJyc7CiAgY29uc3QgZXh0cmFBdHRycyA9IG9wdHMu"
    "ZXh0cmFBdHRycyB8fCAnJzsKICBjb25zdCBib2R5ID0gY2FyZFN2Z0lubmVyKGMpOwogIHJldHVy"
    "biBgPGRpdiBjbGFzcz0iY2FyZC1zdmctb3V0ZXIke2dsb3dDbGFzcyA/ICcgJytnbG93Q2xhc3Mg"
    "OiAnJ30ke2NsaWNrQ2xzfSIke2lkQXR0cn0gJHtleHRyYUF0dHJzfT4KICAgIDxzdmcgd2lkdGg9"
    "IiR7Q1d9IiBoZWlnaHQ9IiR7Q0h9IiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmci"
    "CiAgICAgICAgIHN0eWxlPSJwb3NpdGlvbjphYnNvbHV0ZTt0b3A6MDtsZWZ0OjA7Ij4ke2JvZHl9"
    "PC9zdmc+CiAgICA8c3BhbiBjbGFzcz0iY2FyZC1jb3JuZXIgdGwiIHN0eWxlPSJjb2xvcjoke2Mu"
    "Y29sb3J9Ij4ke2MubGFiZWx9PC9zcGFuPgogICAgPHNwYW4gY2xhc3M9ImNhcmQtY29ybmVyIGJy"
    "IiBzdHlsZT0iY29sb3I6JHtjLmNvbG9yfSI+JHtjLmxhYmVsfTwvc3Bhbj4KICA8L2Rpdj5gOwp9"
    "CgpmdW5jdGlvbiByZW5kZXJCYWNrKCkgewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZC1iYWNr"
    "Ij7inKY8L2Rpdj5gOwp9CgovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZAKLy8gIFdFQlNPQ0tFVCBDTElFTlQKLy8g4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCmxldCB3"
    "cyA9IG51bGw7CmxldCBteVBsYXllciA9IC0xOwpsZXQgcm9vbUNvZGUgPSAnJzsKbGV0IHN0YXRl"
    "ICAgID0gbnVsbDsKCmZ1bmN0aW9uIHdzVXJsKCkgewogIGNvbnN0IHByb3RvID0gbG9jYXRpb24u"
    "cHJvdG9jb2wgPT09ICdodHRwczonID8gJ3dzcycgOiAnd3MnOwogIHJldHVybiBgJHtwcm90b306"
    "Ly8ke2xvY2F0aW9uLmhvc3R9L3dzYDsKfQoKZnVuY3Rpb24gY29ubmVjdChmaXJzdE1zZykgewog"
    "IHdzID0gbmV3IFdlYlNvY2tldCh3c1VybCgpKTsKICB3cy5vbm9wZW4gPSAoKSA9PiB7CiAgICB3"
    "cy5zZW5kKEpTT04uc3RyaW5naWZ5KGZpcnN0TXNnKSk7CiAgICBjbGVhckxvYmJ5RXJyb3IoKTsK"
    "ICB9OwogIHdzLm9ubWVzc2FnZSA9IGUgPT4gaGFuZGxlTXNnKEpTT04ucGFyc2UoZS5kYXRhKSk7"
    "CiAgd3Mub25jbG9zZSA9ICgpID0+IHsKICAgIGlmIChteVBsYXllciA+PSAwKSBzaG93RGNCYW5u"
    "ZXIodHJ1ZSk7CiAgICBzZXRUaW1lb3V0KCgpID0+IHJlY29ubmVjdCgpLCAzMDAwKTsKICB9Owog"
    "IHdzLm9uZXJyb3IgPSAoKSA9PiB7fTsKfQoKZnVuY3Rpb24gcmVjb25uZWN0KCkgewogIGlmIChy"
    "b29tQ29kZSAmJiBteVBsYXllciA+PSAwKSB7CiAgICBjb25zdCBuYW1lID0gZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoJ2lucC1uYW1lJykudmFsdWUudHJpbSgpIHx8ICdQbGF5ZXInOwogICAgY29u"
    "bmVjdCh7IGFjdGlvbjonam9pbicsIHJvb206IHJvb21Db2RlLCBuYW1lIH0pOwogIH0KfQoKZnVu"
    "Y3Rpb24gc2VuZChvYmopIHsKICBpZiAod3MgJiYgd3MucmVhZHlTdGF0ZSA9PT0gV2ViU29ja2V0"
    "Lk9QRU4pIHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkob2JqKSk7Cn0KCi8vIOKUgOKUgCBMb2JieSBh"
    "Y3Rpb25zIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgApmdW5jdGlvbiBnZXROYW1lKCkgewogIHJldHVybiAoZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoJ2lucC1uYW1lJykudmFsdWUudHJpbSgpIHx8ICdQbGF5ZXInKS5zbGljZSgwLDIw"
    "KTsKfQpmdW5jdGlvbiBzaG93TG9iYnlFcnJvcihtc2cpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCdsb2JieS1tc2cnKTsKICBlbC50ZXh0Q29udGVudCA9IG1zZzsgZWwu"
    "c3R5bGUuZGlzcGxheT0nYmxvY2snOwp9CmZ1bmN0aW9uIGNsZWFyTG9iYnlFcnJvcigpIHsKICBk"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9iYnktbXNnJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7"
    "Cn0KZnVuY3Rpb24gY3JlYXRlUm9vbSgpIHsgY29ubmVjdCh7IGFjdGlvbjonY3JlYXRlJywgbmFt"
    "ZTogZ2V0TmFtZSgpIH0pOyB9CmZ1bmN0aW9uIGpvaW5Sb29tKCkgICB7CiAgY29uc3QgY29kZSA9"
    "IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdpbnAtcm9vbScpLnZhbHVlLnRvVXBwZXJDYXNlKCku"
    "dHJpbSgpOwogIGlmICghY29kZSkgeyBzaG93TG9iYnlFcnJvcignRW50ZXIgYSByb29tIGNvZGUu"
    "Jyk7IHJldHVybjsgfQogIGNvbm5lY3QoeyBhY3Rpb246J2pvaW4nLCByb29tOmNvZGUsIG5hbWU6"
    "IGdldE5hbWUoKSB9KTsKfQoKLy8g4pSA4pSAIEdhbWUgYWN0aW9ucyDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24g"
    "ZHJhd0RlY2soKSAgICAgeyBzZW5kKHthY3Rpb246J2RyYXdfZGVjayd9KTsgfQpmdW5jdGlvbiBk"
    "cmF3RGlzY2FyZCgpICB7IHNlbmQoe2FjdGlvbjonZHJhd19kaXNjYXJkJ30pOyB9CmZ1bmN0aW9u"
    "IGRpc2NhcmQoaWR4KSAgIHsgc2VuZCh7YWN0aW9uOidkaXNjYXJkJywgaWR4fSk7IH0KZnVuY3Rp"
    "b24gc2VuZE1vdmUoaSxqKSAgeyBzZW5kKHthY3Rpb246J21vdmUnLCBpLCBqfSk7IH0KZnVuY3Rp"
    "b24gc2VuZERlY2xhcmUoKSAgeyBzZW5kKHthY3Rpb246J2RlY2xhcmUnfSk7IH0KZnVuY3Rpb24g"
    "c2VuZE5leHRIYW5kKCkgeyBjbG9zZVJlc3VsdCgpOyBzZW5kKHthY3Rpb246J25leHRfaGFuZCd9"
    "KTsgfQpmdW5jdGlvbiBzZW5kUmVzZXQoKSAgICB7IGNsb3NlUmVzdWx0KCk7IHNlbmQoe2FjdGlv"
    "bjoncmVzZXQnfSk7IH0KCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyAgTUVTU0FHRSBIQU5ETEVSCi8vIOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkApmdW5jdGlv"
    "biBoYW5kbGVNc2cobXNnKSB7CiAgc2hvd0RjQmFubmVyKGZhbHNlKTsKCiAgaWYgKG1zZy50eXBl"
    "ID09PSAnY3JlYXRlZCcpIHsKICAgIG15UGxheWVyID0gMDsKICAgIHJvb21Db2RlID0gbXNnLnJv"
    "b207CiAgICBzaG93R2FtZSgpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2hkci1yb29t"
    "JykudGV4dENvbnRlbnQgPSBtc2cucm9vbTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdt"
    "c2ctYm94JykudGV4dENvbnRlbnQgPQogICAgICBgUm9vbSAke21zZy5yb29tfSBjcmVhdGVkISBX"
    "YWl0aW5nIGZvciBvcHBvbmVudOKApmA7CiAgICByZXR1cm47CiAgfQogIGlmIChtc2cudHlwZSA9"
    "PT0gJ2pvaW5lZCcpIHsKICAgIG15UGxheWVyID0gbXNnLnBsYXllcjsKICAgIHJvb21Db2RlID0g"
    "bXNnLnJvb207CiAgICBzaG93R2FtZSgpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2hk"
    "ci1yb29tJykudGV4dENvbnRlbnQgPSBtc2cucm9vbTsKICAgIHJldHVybjsKICB9CiAgaWYgKG1z"
    "Zy50eXBlID09PSAnbG9iYnknKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnaGRyLXJv"
    "b20nKS50ZXh0Q29udGVudCA9IG1zZy5yb29tOwogICAgaWYgKG1zZy53YWl0aW5nKSB7CiAgICAg"
    "IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtc2ctYm94JykudGV4dENvbnRlbnQgPQogICAgICAg"
    "IGBSb29tICR7bXNnLnJvb219IOKAlCB3YWl0aW5nIGZvciBvcHBvbmVudOKApmA7CiAgICB9CiAg"
    "ICByZXR1cm47CiAgfQogIGlmIChtc2cudHlwZSA9PT0gJ2Vycm9yJykgeyBzaG93TG9iYnlFcnJv"
    "cihtc2cubXNnKTsgcmV0dXJuOyB9CiAgaWYgKG1zZy50eXBlID09PSAncGxheWVyX2xlZnQnKSB7"
    "CiAgICBzaG93RGNCYW5uZXIodHJ1ZSwgbXNnLm1zZyk7IHJldHVybjsKICB9CiAgaWYgKG1zZy50"
    "eXBlID09PSAncG9uZycpIHJldHVybjsKICBpZiAobXNnLnR5cGUgPT09ICdzdGF0ZScpIHsKICAg"
    "IHN0YXRlID0gbXNnOwogICAgcmVuZGVyU3RhdGUobXNnKTsKICAgIHJldHVybjsKICB9Cn0KCi8v"
    "IOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkAovLyAgUkVOREVSCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkApmdW5jdGlvbiBzaG93R2FtZSgpIHsKICBkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgnbG9iYnknKS5zdHlsZS5kaXNwbGF5ID0gJ25vbmUnOwogIGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCdnYW1lJykuc3R5bGUuZGlzcGxheSAgPSAnZmxleCc7Cn0KCmZ1bmN0"
    "aW9uIHNjb3JlQ29sb3IocHRzKSB7CiAgaWYgKHB0cyA+PSA4MCkgcmV0dXJuICdzLXJlZCc7CiAg"
    "aWYgKHB0cyA+PSA1MCkgcmV0dXJuICdzLW9yYW5nZSc7CiAgcmV0dXJuICdzLWdyZWVuJzsKfQoK"
    "ZnVuY3Rpb24gcmVuZGVyU3RhdGUocykgewogIC8vIFNjb3JlcwogIGNvbnN0IG5hbWVzID0gcy5u"
    "YW1lczsKICBmb3IgKGxldCBpPTA7aTwyO2krKykgewogICAgY29uc3QgZWwgPSBkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZChgc2NvcmUke2l9LXZhbGApOwogICAgZWwudGV4dENvbnRlbnQgPSBzLnNj"
    "b3Jlc1tpXTsKICAgIGVsLmNsYXNzTmFtZSA9IGBzdmFsICR7c2NvcmVDb2xvcihzLnNjb3Jlc1tp"
    "XSl9YDsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGBzY29yZSR7aX0tbmFtZWApLnRleHRD"
    "b250ZW50ID0gbmFtZXNbaV0gfHwgJ+KAlCc7CiAgfQoKICAvLyBUdXJuIGJhbm5lcgogIGNvbnN0"
    "IHRiID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3R1cm4tYmFubmVyJyk7CiAgaWYgKHMubXlf"
    "dHVybikgewogICAgdGIudGV4dENvbnRlbnQgPSAn8J+foiBZb3VyIHR1cm4nOwogICAgdGIuY2xh"
    "c3NOYW1lID0gJ3R1cm4tbWluZSc7CiAgfSBlbHNlIHsKICAgIHRiLnRleHRDb250ZW50ID0gYOKP"
    "syAke25hbWVzWzEtbXlQbGF5ZXJdfSdzIHR1cm5gOwogICAgdGIuY2xhc3NOYW1lID0gJ3R1cm4t"
    "b3BwJzsKICB9CgogIC8vIERlY2xhcmUgV2luIGJ1dHRvbgogIGNvbnN0IGJ0bkRlY2wgPSBkb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgnYnRuLWRlY2xhcmUnKTsKICBidG5EZWNsLnN0eWxlLmRpc3Bs"
    "YXkgPSBzLmNhbl9kZWNsYXJlID8gJycgOiAnbm9uZSc7CgogIC8vIE1lc3NhZ2UKICBjb25zdCBt"
    "c2dCb3ggPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXNnLWJveCcpOwogIG1zZ0JveC50ZXh0"
    "Q29udGVudCA9IHMubWVzc2FnZTsKICBtc2dCb3guY2xhc3NOYW1lID0gJ21zZy1ib3gnOwogIGlm"
    "IChzLnJlc3VsdCkgewogICAgaWYgKHMucmVzdWx0LmluY2x1ZGVzKGBwJHtteVBsYXllcn1gKSkg"
    "bXNnQm94LmNsYXNzTGlzdC5hZGQocy5yZXN1bHQuaW5jbHVkZXMoJ2NjJykgPyAnbXNnLWNjJyA6"
    "ICdtc2ctd2luJyk7CiAgICBlbHNlIG1zZ0JveC5jbGFzc0xpc3QuYWRkKCdtc2ctbG9zZScpOwog"
    "IH0KCiAgLy8gT3Bwb25lbnQgbGFiZWwgKyBoYW5kCiAgY29uc3Qgb3BwSWR4ID0gcy5vcHBfaWR4"
    "OwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdvcHAtbGFiZWwnKS50ZXh0Q29udGVudCA9CiAg"
    "ICBg8J+kliAke25hbWVzW29wcElkeF19ICgke3Mub3BwX2hhbmQubGVuZ3RofSBjYXJkcylgOwog"
    "IGNvbnN0IG9wcEhhbmRFbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdvcHAtaGFuZCcpOwog"
    "IG9wcEhhbmRFbC5pbm5lckhUTUwgPSAnJzsKICBmb3IgKGNvbnN0IGMgb2Ygcy5vcHBfaGFuZCkg"
    "ewogICAgaWYgKGMuZmFjZWRvd24pIHsKICAgICAgb3BwSGFuZEVsLmluc2VydEFkamFjZW50SFRN"
    "TCgnYmVmb3JlZW5kJywgcmVuZGVyQmFjaygpKTsKICAgIH0gZWxzZSB7CiAgICAgIGNvbnN0IGdj"
    "ID0gYy5pc193aW4gPyAnZ2xvdy1tZWxkJyA6ICcnOwogICAgICBvcHBIYW5kRWwuaW5zZXJ0QWRq"
    "YWNlbnRIVE1MKCdiZWZvcmVlbmQnLCByZW5kZXJDYXJkKGMsIHtnbG93Q2xhc3M6IGdjfSkpOwog"
    "ICAgfQogIH0KCiAgLy8gRGVjayBjYXJkIChjbGlja2FibGUgb25seSBvbiB5b3VyIGRyYXcgdHVy"
    "bikKICBjb25zdCBjYW5EcmF3RGVjayA9IHMubXlfdHVybiAmJiBzLnBoYXNlID09PSAnZHJhdyc7"
    "CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2RlY2stY2FyZCcpLmNsYXNzTmFtZSA9CiAgICBg"
    "Y2FyZC1iYWNrJHtjYW5EcmF3RGVjayA/ICcgY2xpY2thYmxlJyA6ICcnfWA7CiAgZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoJ2RlY2stY2FyZCcpLm9uY2xpY2sgPSBjYW5EcmF3RGVjayA/IGRyYXdE"
    "ZWNrIDogbnVsbDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZGVjay1jb3VudCcpLnRleHRD"
    "b250ZW50ID0gYCR7cy5kZWNrX3NpemV9IGNhcmRzYDsKCiAgLy8gRGlzY2FyZCBwaWxlIHRvcAog"
    "IGNvbnN0IGRpc2NFbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkaXNjYXJkLWNhcmQnKTsK"
    "ICBpZiAocy5kaXNjYXJkX3RvcCkgewogICAgY29uc3QgY2FuVGFrZSA9IHMubXlfdHVybiAmJiBz"
    "LnBoYXNlID09PSAnZHJhdyc7CiAgICBjb25zdCBnYyA9ICcnOwogICAgZGlzY0VsLmlubmVySFRN"
    "TCA9IGA8ZGl2IG9uY2xpY2s9IiR7Y2FuVGFrZSA/ICdkcmF3RGlzY2FyZCgpJyA6ICcnfSIgc3R5"
    "bGU9ImN1cnNvcjoke2NhblRha2U/J3BvaW50ZXInOidkZWZhdWx0J30iPmAgKwogICAgICAgICAg"
    "ICAgICAgICAgICAgICByZW5kZXJDYXJkKHMuZGlzY2FyZF90b3AsIHtjbGlja2FibGU6IGNhblRh"
    "a2V9KSArICc8L2Rpdj4nOwogIH0gZWxzZSB7CiAgICBkaXNjRWwuaW5uZXJIVE1MID0gYDxkaXYg"
    "c3R5bGU9IndpZHRoOjcycHg7aGVpZ2h0OjEwOHB4O2JvcmRlcjoxcHggZGFzaGVkIHJnYmEoMjU1"
    "LDI1NSwyNTUsLjIpO2JvcmRlci1yYWRpdXM6N3B4O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LC4yNSk7"
    "Zm9udC1zaXplOi43NXJlbTsiPkVtcHR5PC9kaXY+YDsKICB9CgogIC8vIE15IGhhbmQKICByZW5k"
    "ZXJNeUhhbmQocyk7CgogIC8vIFJlc3VsdCBvdmVybGF5CiAgY29uc3QgZmluaXNoZWQgPSBbJ2hh"
    "bmRfb3ZlcicsJ21hdGNoX292ZXInXS5pbmNsdWRlcyhzLnBoYXNlKTsKICBpZiAoZmluaXNoZWQp"
    "IHNob3dSZXN1bHQocyk7Cn0KCi8vIOKUgOKUgCBEcmFnIHN0YXRlIOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gApsZXQgX2RyYWdTcmMgPSBudWxsOwoKZnVuY3Rpb24gb25EcmFnU3RhcnQoZSwgaWR4KSB7CiAg"
    "X2RyYWdTcmMgPSBpZHg7CiAgZS5kYXRhVHJhbnNmZXIuZWZmZWN0QWxsb3dlZCA9ICdtb3ZlJzsK"
    "ICBlLmRhdGFUcmFuc2Zlci5zZXREYXRhKCd0ZXh0L3BsYWluJywgU3RyaW5nKGlkeCkpOwogIHNl"
    "dFRpbWVvdXQoKCkgPT4gewogICAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgn"
    "bXljYXJkLScgKyBpZHgpOwogICAgaWYgKGVsKSBlbC5jbGFzc0xpc3QuYWRkKCdkcmFnZ2luZycp"
    "OwogIH0sIDApOwp9CmZ1bmN0aW9uIG9uRHJhZ092ZXIoZSwgaWR4KSB7CiAgZS5wcmV2ZW50RGVm"
    "YXVsdCgpOwogIGUuZGF0YVRyYW5zZmVyLmRyb3BFZmZlY3QgPSAnbW92ZSc7Cn0KZnVuY3Rpb24g"
    "b25EcmFnRW50ZXIoZSwgaWR4KSB7CiAgaWYgKF9kcmFnU3JjICE9PSBudWxsICYmIF9kcmFnU3Jj"
    "ICE9PSBpZHgpIHsKICAgIGNvbnN0IGVsID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ215Y2Fy"
    "ZC0nICsgaWR4KTsKICAgIGlmIChlbCkgZWwuY2xhc3NMaXN0LmFkZCgnZHJhZy1vdmVyJyk7CiAg"
    "fQp9CmZ1bmN0aW9uIG9uRHJhZ0xlYXZlKGUsIGlkeCkgewogIGNvbnN0IGVsID0gZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoJ215Y2FyZC0nICsgaWR4KTsKICBpZiAoZWwpIGVsLmNsYXNzTGlzdC5y"
    "ZW1vdmUoJ2RyYWctb3ZlcicpOwp9CmZ1bmN0aW9uIG9uRHJvcChlLCBpZHgpIHsKICBlLnByZXZl"
    "bnREZWZhdWx0KCk7CiAgY29uc3Qgc3JjID0gX2RyYWdTcmM7IF9kcmFnU3JjID0gbnVsbDsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcuZHJhZy1vdmVyJykuZm9yRWFjaChlbCA9PiBlbC5j"
    "bGFzc0xpc3QucmVtb3ZlKCdkcmFnLW92ZXInKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgnLmRyYWdnaW5nJykuZm9yRWFjaChlbCA9PiBlbC5jbGFzc0xpc3QucmVtb3ZlKCdkcmFnZ2lu"
    "ZycpKTsKICBpZiAoc3JjICE9PSBudWxsICYmIHNyYyAhPT0gaWR4KSBzZW5kTW92ZShzcmMsIGlk"
    "eCk7Cn0KZnVuY3Rpb24gb25EcmFnRW5kKGUsIGlkeCkgewogIF9kcmFnU3JjID0gbnVsbDsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcuZHJhZ2dpbmcnKS5mb3JFYWNoKGVsID0+IGVsLmNs"
    "YXNzTGlzdC5yZW1vdmUoJ2RyYWdnaW5nJykpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "Jy5kcmFnLW92ZXInKS5mb3JFYWNoKGVsID0+IGVsLmNsYXNzTGlzdC5yZW1vdmUoJ2RyYWctb3Zl"
    "cicpKTsKfQoKZnVuY3Rpb24gcmVuZGVyTXlIYW5kKHMpIHsKICBjb25zdCBoYW5kICAgICAgID0g"
    "cy5teV9oYW5kOwogIGNvbnN0IHBoYXNlICAgICAgPSBzLnBoYXNlOwogIGNvbnN0IGNhbkRpc2Nh"
    "cmQgPSBzLm15X3R1cm4gJiYgcGhhc2UgPT09ICdkaXNjYXJkJzsKICBjb25zdCBjYW5EcmFnICAg"
    "ID0gWydkcmF3JywnZGlzY2FyZCddLmluY2x1ZGVzKHBoYXNlKTsgICAvLyBkcmFnIGFsbG93ZWQg"
    "YWx3YXlzIGluIGFjdGl2ZSBwaGFzZQogIGNvbnN0IHdpblNldCAgICAgPSBuZXcgU2V0KHMud2lu"
    "X2lkeC5tYXAoKFtpXSkgPT4gaSkpOwogIGNvbnN0IHdpbkNDICAgICAgPSBuZXcgU2V0KHMud2lu"
    "X2lkeC5maWx0ZXIoKFssY2NdKSA9PiBjYykubWFwKChbaV0pID0+IGkpKTsKCiAgY29uc3QgaGFu"
    "ZFJvdyA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdteS1oYW5kLXJvdycpOwogIGNvbnN0IGRp"
    "c2NSb3cgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZGlzY2FyZC1yb3cnKTsKICBoYW5kUm93"
    "LmlubmVySFRNTCA9ICcnOwogIGRpc2NSb3cuaW5uZXJIVE1MID0gJyc7CgogIGhhbmQuZm9yRWFj"
    "aCgoYywgaSkgPT4gewogICAgbGV0IGdjID0gJyc7CiAgICBpZiAoYy5pc19uZXcpIGdjID0gJ2ds"
    "b3ctbmV3JzsKICAgIGVsc2UgaWYgKGMuaXNfY2MpIGdjID0gJ2dsb3ctY2MnOwogICAgZWxzZSBp"
    "ZiAoYy5pc193aW4pIGdjID0gJ2dsb3ctd2luJzsKCiAgICBjb25zdCBkcmFnQXR0cnMgPSBjYW5E"
    "cmFnCiAgICAgID8gYGRyYWdnYWJsZT0idHJ1ZSIKICAgICAgICAgb25kcmFnc3RhcnQ9Im9uRHJh"
    "Z1N0YXJ0KGV2ZW50LCR7aX0pIgogICAgICAgICBvbmRyYWdvdmVyPSJvbkRyYWdPdmVyKGV2ZW50"
    "LCR7aX0pIgogICAgICAgICBvbmRyYWdlbnRlcj0ib25EcmFnRW50ZXIoZXZlbnQsJHtpfSkiCiAg"
    "ICAgICAgIG9uZHJhZ2xlYXZlPSJvbkRyYWdMZWF2ZShldmVudCwke2l9KSIKICAgICAgICAgb25k"
    "cm9wPSJvbkRyb3AoZXZlbnQsJHtpfSkiCiAgICAgICAgIG9uZHJhZ2VuZD0ib25EcmFnRW5kKGV2"
    "ZW50LCR7aX0pImAKICAgICAgOiAnJzsKCiAgICBoYW5kUm93Lmluc2VydEFkamFjZW50SFRNTCgn"
    "YmVmb3JlZW5kJywKICAgICAgYDxkaXYgY2xhc3M9ImNhcmQtd3JhcCI+JHtyZW5kZXJDYXJkKGMs"
    "IHtnbG93Q2xhc3M6IGdjLCBjYXJkSWQ6J215Y2FyZC0nK2ksIGV4dHJhQXR0cnM6IGRyYWdBdHRy"
    "c30pfTwvZGl2PmApOwogIH0pOwoKICBpZiAoY2FuRGlzY2FyZCkgewogICAgaGFuZC5mb3JFYWNo"
    "KChjLCBpKSA9PiB7CiAgICAgIGNvbnN0IGNvbCAgPSBkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdk"
    "aXYnKTsKICAgICAgY29uc3QgaXNDQyAgPSB3aW5DQy5oYXMoaSk7CiAgICAgIGNvbnN0IGlzV2lu"
    "ID0gd2luU2V0LmhhcyhpKTsKICAgICAgbGV0IGNscyA9ICcnLCBsYmwgPSAn4oaTJzsKICAgICAg"
    "aWYgKGlzQ0MpICAgICAgIHsgY2xzID0gJ2QtY2hpbmNob24nOyBsYmwgPSAn8J+PhSc7IH0KICAg"
    "ICAgZWxzZSBpZiAoaXNXaW4pIHsgY2xzID0gJ2Qtc3RvcCc7ICAgICBsYmwgPSAn4pyLJzsgfQog"
    "ICAgICBjb2wuaW5uZXJIVE1MID0gYDxidXR0b24gY2xhc3M9ImRpc2MtYnRuICR7Y2xzfSIgb25j"
    "bGljaz0iZGlzY2FyZCgke2l9KSI+JHtsYmx9PC9idXR0b24+YDsKICAgICAgZGlzY1Jvdy5hcHBl"
    "bmRDaGlsZChjb2wpOwogICAgfSk7CiAgfQp9CgovLyDilIDilIAgUmVzdWx0IG92ZXJsYXkg4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1"
    "bmN0aW9uIHNob3dSZXN1bHQocykgewogIGNvbnN0IHJlc3VsdCA9IHMucmVzdWx0IHx8ICcnOwog"
    "IGNvbnN0IG15V2luICA9IHJlc3VsdC5pbmNsdWRlcyhgcCR7bXlQbGF5ZXJ9YCkgJiYgIXJlc3Vs"
    "dC5pbmNsdWRlcygnZGVjbGFyZScpOwogIGNvbnN0IGlzQ0MgICA9IHJlc3VsdC5pbmNsdWRlcygn"
    "X2NjJyk7CiAgY29uc3QgaXNEY2wgID0gcmVzdWx0ID09PSAnZGVjbGFyZSc7CiAgY29uc3QgaXNN"
    "YXRjaE92ZXIgPSBzLnBoYXNlID09PSAnbWF0Y2hfb3Zlcic7CgogIGNvbnN0IG92ZXJsYXkgPSBk"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgncmVzdWx0LW92ZXJsYXknKTsKICBvdmVybGF5LnN0eWxl"
    "LmRpc3BsYXkgPSAnZmxleCc7CgogIC8vIEVtb2ppICsgdGl0bGUKICBkb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgncmVzLWVtb2ppJykudGV4dENvbnRlbnQgPQogICAgaXNDQyA/ICfwn4+FJyA6IG15"
    "V2luID8gJ/CfjoknIDogaXNEY2wgPyAn8J+Xku+4jycgOiAn8J+YlCc7CiAgZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoJ3Jlcy10aXRsZScpLnRleHRDb250ZW50ID0gcy5tZXNzYWdlOwogIGRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCdyZXMtdGl0bGUnKS5zdHlsZS5jb2xvciA9CiAgICBpc0NDID8g"
    "JyNmYmJmMjQnIDogbXlXaW4gPyAnIzg2ZWZhYycgOiBpc0RjbCA/ICcjZmI5MjNjJyA6ICcjZmNh"
    "NWE1JzsKCiAgLy8gUGVuYWx0aWVzCiAgY29uc3QgcGVuRGl2ID0gZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoJ3Jlcy1wZW5hbHRpZXMnKTsKICBwZW5EaXYuaW5uZXJIVE1MID0gJyc7CiAgY29uc3Qg"
    "cGVucyA9IHMucGVuYWx0aWVzIHx8IFtudWxsLCBudWxsXTsKICBbMCwxXS5mb3JFYWNoKGkgPT4g"
    "ewogICAgaWYgKHBlbnNbaV0gPT09IG51bGwgfHwgcGVuc1tpXSA9PT0gJ2NjJykgcmV0dXJuOwog"
    "ICAgY29uc3QgYm94ID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7CiAgICBib3guY2xh"
    "c3NOYW1lID0gJ3Blbi1ib3gnOwogICAgY29uc3QgdmFsID0gcGVuc1tpXTsKICAgIGNvbnN0IGNv"
    "bCA9IHZhbCA8IDAgPyAnIzg2ZWZhYycgOiB2YWwgPT09IDAgPyAncmdiYSgyNTUsMjU1LDI1NSwu"
    "NSknIDogJyNmY2E1YTUnOwogICAgY29uc3Qgc2lnbiA9IHZhbCA+IDAgPyAnKycgOiAnJzsKICAg"
    "IGJveC5pbm5lckhUTUwgPSBgPGRpdiBjbGFzcz0icGVuLW5hbWUiPiR7cy5uYW1lc1tpXX08L2Rp"
    "dj4KICAgICAgICAgICAgICAgICAgICAgPGRpdiBjbGFzcz0icGVuLXZhbCIgc3R5bGU9ImNvbG9y"
    "OiR7Y29sfSI+JHtzaWdufSR7dmFsfSBwdHM8L2Rpdj5gOwogICAgcGVuRGl2LmFwcGVuZENoaWxk"
    "KGJveCk7CiAgfSk7CgogIC8vIE1lbGRzCiAgY29uc3QgbWVsZHNEaXYgPSBkb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgncmVzLW1lbGRzJyk7CiAgbWVsZHNEaXYuaW5uZXJIVE1MID0gJyc7CiAgaWYg"
    "KHMubWVsZHMgJiYgcy5tZWxkcy5sZW5ndGgpIHsKICAgIGZvciAoY29uc3QgbSBvZiBzLm1lbGRz"
    "KSB7CiAgICAgIGNvbnN0IGJsb2NrID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7CiAg"
    "ICAgIGJsb2NrLmNsYXNzTmFtZSA9ICdtZWxkLWJsb2NrJzsKICAgICAgYmxvY2suaW5uZXJIVE1M"
    "ID0gYDxkaXYgY2xhc3M9Im1lbGQtbGFiZWwiPiR7bS5raW5kfTwvZGl2PgogICAgICAgIDxkaXYg"
    "Y2xhc3M9Im1lbGQtcm93Ij4ke20uY2FyZHMubWFwKGMgPT4gcmVuZGVyQ2FyZChjKSkuam9pbign"
    "Jyl9PC9kaXY+YDsKICAgICAgbWVsZHNEaXYuYXBwZW5kQ2hpbGQoYmxvY2spOwogICAgfQogIH0K"
    "CiAgLy8gVW5tYXRjaGVkIGNhcmRzCiAgY29uc3QgdW5tRGl2ID0gZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoJ3Jlcy11bm1hdGNoZWQnKTsKICB1bm1EaXYuaW5uZXJIVE1MID0gJyc7CiAgY29uc3Qg"
    "YWRkVW5tID0gKGNhcmRzLCB3aG8sIHB0cykgPT4gewogICAgaWYgKCFjYXJkcyB8fCBjYXJkcy5s"
    "ZW5ndGggPT09IDApIHJldHVybjsKICAgIGNvbnN0IGJsb2NrID0gZG9jdW1lbnQuY3JlYXRlRWxl"
    "bWVudCgnZGl2Jyk7CiAgICBibG9jay5jbGFzc05hbWUgPSAnbWVsZC1ibG9jayc7CiAgICBibG9j"
    "ay5pbm5lckhUTUwgPSBgPGRpdiBjbGFzcz0ibWVsZC1sYWJlbCI+JHt3aG99IHVubWF0Y2hlZCAo"
    "KyR7cHRzfSBwdHMpPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Im1lbGQtcm93Ij4ke2NhcmRzLm1h"
    "cChjID0+IHJlbmRlckNhcmQoYykpLmpvaW4oJycpfTwvZGl2PmA7CiAgICB1bm1EaXYuYXBwZW5k"
    "Q2hpbGQoYmxvY2spOwogIH07CiAgY29uc3Qgb3BwUGVuID0gcy5wZW5hbHRpZXMgPyBzLnBlbmFs"
    "dGllc1tzLm9wcF9pZHhdIDogbnVsbDsKICBjb25zdCBteVBlbiAgPSBzLnBlbmFsdGllcyA/IHMu"
    "cGVuYWx0aWVzW215UGxheWVyXSAgOiBudWxsOwogIGFkZFVubShzLnVubWF0Y2hlZF9taW5lLCAn"
    "WW91cicsIG15UGVuIHx8IDApOwogIGFkZFVubShzLnVubWF0Y2hlZF9vcHAsIHMubmFtZXNbcy5v"
    "cHBfaWR4XSwgb3BwUGVuIHx8IDApOwoKICAvLyBOZXh0IGJ1dHRvbiBsYWJlbAogIGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCdidG4tbmV4dCcpLnRleHRDb250ZW50ID0KICAgIGlzTWF0Y2hPdmVy"
    "ID8gJ/Cfjq4gTmV3IE1hdGNoJyA6ICfilrYgTmV4dCBIYW5kJzsKfQoKZnVuY3Rpb24gY2xvc2VS"
    "ZXN1bHQoKSB7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Jlc3VsdC1vdmVybGF5Jykuc3R5"
    "bGUuZGlzcGxheSA9ICdub25lJzsKfQoKLy8g4pSA4pSAIFVJIGhlbHBlcnMg4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "CmZ1bmN0aW9uIHNob3dEY0Jhbm5lcihzaG93LCBtc2cpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCdkYy1iYW5uZXInKTsKICBlbC5zdHlsZS5kaXNwbGF5ID0gc2hvdyA/"
    "ICcnIDogJ25vbmUnOwogIGlmIChtc2cpIGVsLnRleHRDb250ZW50ID0gbXNnOwp9CgovLyDilIDi"
    "lIAgSGVhcnRiZWF0IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApzZXRJbnRlcnZhbCgoKSA9PiB7IGlmICh3cyAm"
    "JiB3cy5yZWFkeVN0YXRlID09PSBXZWJTb2NrZXQuT1BFTikgc2VuZCh7YWN0aW9uOidwaW5nJ30p"
    "OyB9LCAyMDAwMCk7CgovLyDilIDilIAgS2V5Ym9hcmQgc2hvcnRjdXRzIOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApkb2N1bWVudC5hZGRFdmVudExpc3RlbmVy"
    "KCdrZXlkb3duJywgZSA9PiB7CiAgaWYgKCFzdGF0ZSB8fCAhc3RhdGUubXlfdHVybikgcmV0dXJu"
    "OwogIC8vIEVudGVyIGluIGxvYmJ5IG5hbWUgZmllbGQg4oaSIGNyZWF0ZSByb29tCiAgY29uc3Qg"
    "Zm9jdXNlZCA9IGRvY3VtZW50LmFjdGl2ZUVsZW1lbnQ7CiAgaWYgKGZvY3VzZWQgJiYgZm9jdXNl"
    "ZC5pZCA9PT0gJ2lucC1uYW1lJyAmJiBlLmtleSA9PT0gJ0VudGVyJykgY3JlYXRlUm9vbSgpOwog"
    "IGlmIChmb2N1c2VkICYmIGZvY3VzZWQuaWQgPT09ICdpbnAtcm9vbScgJiYgZS5rZXkgPT09ICdF"
    "bnRlcicpIGpvaW5Sb29tKCk7Cn0pOwo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+Cg=="
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
