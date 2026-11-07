"""Stand-in for `codex app-server` that speaks the same JSON-RPC subset as codex-cli 0.167.0.

Used only by tests. Behaviour is chosen by the turn input text:
- contains WRITE   -> sends a file-change approval request and finishes according to the answer
- contains SLOW    -> streams slowly until interrupted
- otherwise        -> streams "CODEX OK"
"""

import json
import sys
import time

THREAD = "thread-test-1"
pending_turn = None  # (turn_id, text, approval_id) while waiting on an approval answer
interrupted = set()


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method, params):
    send({"method": method, "params": params})


def finish(turn_id, status, text=None):
    if text:
        notify(
            "item/completed",
            {"item": {"type": "agentMessage", "id": "m1", "text": text}, "threadId": THREAD, "turnId": turn_id},
        )
    notify("turn/completed", {"threadId": THREAD, "turn": {"id": turn_id, "status": status, "error": None}})


def stream(turn_id, words):
    for word in words:
        if turn_id in interrupted:
            return False
        notify("item/agentMessage/delta", {"threadId": THREAD, "turnId": turn_id, "itemId": "m1", "delta": word})
        time.sleep(0.02)
    return True


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        send(
            {
                "id": request_id,
                "result": {"userAgent": "fake", "codexHome": "x", "platformFamily": "unix", "platformOs": "linux"},
            }
        )
    elif method == "initialized":
        continue
    elif method in ("thread/start", "thread/resume"):
        send({"id": request_id, "result": {"thread": {"id": THREAD}, "model": "fake", "cwd": "."}})
    elif method == "turn/start":
        text = message["params"]["input"][0]["text"]
        turn_id = "turn-1"
        send({"id": request_id, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
        notify("turn/started", {"threadId": THREAD, "turn": {"id": turn_id, "status": "inProgress"}})
        if "WRITE" in text:
            if stream(turn_id, ["I will ", "make the change."]):
                # Server-initiated request: the client must answer with a decision.
                send(
                    {
                        "id": 77,
                        "method": "item/fileChange/requestApproval",
                        "params": {
                            "itemId": "fc1",
                            "threadId": THREAD,
                            "turnId": turn_id,
                            "startedAtMs": 1,
                            "reason": "write notes.txt",
                        },
                    }
                )
                pending_turn = (turn_id, text, 77)
        elif "SLOW" in text:
            if stream(turn_id, ["tick " + str(i) + " " for i in range(200)]):
                finish(turn_id, "completed", "finished")
        else:
            if stream(turn_id, ["CODEX", " OK"]):
                finish(turn_id, "completed", "CODEX OK")
    elif method == "turn/interrupt":
        turn_id = message["params"]["turnId"]
        interrupted.add(turn_id)
        send({"id": request_id, "result": {}})
        finish(turn_id, "interrupted")
    elif method is None and request_id == 77 and pending_turn:
        decision = (message.get("result") or {}).get("decision")
        turn_id, _, _ = pending_turn
        pending_turn = None
        if decision == "accept":
            finish(turn_id, "completed", "change applied")
        else:
            finish(turn_id, "completed", "change declined")
    elif request_id is not None:
        send({"id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}})
