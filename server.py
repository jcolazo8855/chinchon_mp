"""
server.py — Chinchón multiplayer server
FastAPI + WebSockets.  Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import random
import string
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import game_logic as gl

# ── App setup ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Chinchón Multiplayer")

# Serve static files (index.html)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html)


# ── Room management ────────────────────────────────────────────────────────────
class Room:
    def __init__(self, code: str):
        self.code      = code
        self.sockets:  list[WebSocket]  = []   # max 2
        self.names:    list[str]        = []
        self.game:     dict | None      = None
        self.created   = time.time()

    @property
    def n_players(self):
        return len(self.sockets)

    def player_idx(self, ws: WebSocket) -> int:
        return self.sockets.index(ws)


rooms: dict[str, Room] = {}


def _gen_code() -> str:
    while True:
        code = ''.join(random.choices(string.ascii_uppercase, k=4))
        if code not in rooms:
            return code


def _cleanup_old_rooms():
    """Remove rooms idle for more than 2 hours."""
    cutoff = time.time() - 7200
    stale  = [k for k, r in rooms.items() if r.created < cutoff and r.n_players == 0]
    for k in stale:
        del rooms[k]


# ── Broadcast helpers ──────────────────────────────────────────────────────────
async def _broadcast(room: Room):
    """Send each player their personalised game view."""
    if room.game is None:
        return
    for idx, ws in enumerate(room.sockets):
        payload = gl.player_view(room.game, idx)
        payload['type'] = 'state'
        try:
            await ws.send_json(payload)
        except Exception:
            pass


async def _send(ws: WebSocket, msg: dict):
    try:
        await ws.send_json(msg)
    except Exception:
        pass


async def _send_lobby(room: Room):
    """Tell all sockets the lobby state (waiting for 2nd player)."""
    for ws in room.sockets:
        await _send(ws, {
            'type':    'lobby',
            'room':    room.code,
            'names':   room.names,
            'waiting': room.n_players < 2,
        })


# ── WebSocket endpoint ─────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    room: Room | None = None
    player: int       = -1

    try:
        # ── First message must be join/create ──────────────────────────────
        raw  = await asyncio.wait_for(websocket.receive_text(), timeout=30)
        msg  = json.loads(raw)
        name = (msg.get('name') or 'Player').strip()[:20] or 'Player'
        action = msg.get('action', '')

        if action == 'create':
            _cleanup_old_rooms()
            code = _gen_code()
            room = Room(code)
            rooms[code] = room
            room.sockets.append(websocket)
            room.names.append(name)
            player = 0
            log.info("Room %s created by %s", code, name)
            await _send(websocket, {'type': 'created', 'room': code, 'player': 0})
            await _send_lobby(room)

        elif action == 'join':
            code = (msg.get('room') or '').upper().strip()
            if code not in rooms:
                await _send(websocket, {'type': 'error', 'msg': f'Room {code} not found.'})
                await websocket.close()
                return
            room = rooms[code]
            if room.n_players >= 2:
                await _send(websocket, {'type': 'error', 'msg': 'Room is full.'})
                await websocket.close()
                return
            room.sockets.append(websocket)
            room.names.append(name)
            player = 1
            log.info("Room %s: %s joined", code, name)

            # Both players connected — start the game
            room.game = gl.new_game(room.names)
            await _send(websocket, {'type': 'joined', 'room': code, 'player': 1})
            await _broadcast(room)

        else:
            await _send(websocket, {'type': 'error', 'msg': 'First message must be create or join.'})
            await websocket.close()
            return

        # ── Message loop ───────────────────────────────────────────────────
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            act = msg.get('action', '')

            if room.game is None:
                await _send(websocket, {'type': 'error', 'msg': 'Game not started yet.'})
                continue

            ok, err = True, ''

            if act == 'draw_deck':
                ok, err = gl.action_draw_deck(room.game, player)

            elif act == 'draw_discard':
                ok, err = gl.action_draw_discard(room.game, player)

            elif act == 'discard':
                idx = int(msg.get('idx', -1))
                ok, err = gl.action_discard(room.game, player, idx)

            elif act == 'declare':
                ok, err = gl.action_declare(room.game, player)

            elif act == 'move':
                i = int(msg.get('i', 0))
                j = int(msg.get('j', 0))
                ok, err = gl.action_move(room.game, player, i, j)

            elif act == 'next_hand':
                if room.game['phase'] in ('hand_over', 'match_over'):
                    if room.game['phase'] == 'match_over':
                        room.game = gl.new_game(room.names)
                    else:
                        room.game = gl.new_hand(room.game)

            elif act == 'reset':
                room.game = gl.new_game(room.names)

            elif act == 'ping':
                await _send(websocket, {'type': 'pong'})
                continue

            else:
                await _send(websocket, {'type': 'error', 'msg': f'Unknown action: {act}'})
                continue

            if not ok:
                await _send(websocket, {'type': 'error', 'msg': err})
            else:
                await _broadcast(room)

    except WebSocketDisconnect:
        log.info("Player %d disconnected from room %s",
                 player, room.code if room else '?')
    except asyncio.TimeoutError:
        log.info("Timeout waiting for first message")
    except Exception as e:
        log.exception("Unexpected error: %s", e)
    finally:
        if room and websocket in room.sockets:
            room.sockets.remove(websocket)
            if player < len(room.names):
                gone = room.names[player]
                # Notify remaining player
                for ws in room.sockets:
                    await _send(ws, {
                        'type': 'player_left',
                        'msg':  f'{gone} disconnected. Waiting for them to rejoin…'
                    })
            if room.n_players == 0:
                rooms.pop(room.code, None)
                log.info("Room %s closed (empty)", room.code)


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
