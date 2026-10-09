"""Local-only GUI, SQLite snapshots, and actual read-only local LLM agent."""
from __future__ import annotations
import argparse
import json
import math
import os
import sqlite3
import subprocess
import threading
from functools import partial
from contextlib import closing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from agent_backend import AgentService, backend_status
from factory_tools import STATION_IDS

ROOT = Path(__file__).resolve().parent
STAGES = {"intake", "mixing", "quality", "packing", "warehouse", "shipped"}
MAX_BODY = 4 * 1024 * 1024


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_state(state):
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Unsupported state version")
    lots, alerts, events = state.get("lots"), state.get("alerts"), state.get("events")
    if not isinstance(lots, list) or len(lots) > 1000 or not isinstance(alerts, list) or len(alerts) > 5000 or not isinstance(events, list) or len(events) > 500:
        raise ValueError("Invalid record limits")
    if not finite(state.get("tick")) or state["tick"] < 0 or not finite(state.get("speed")) or not 0.25 <= state["speed"] <= 8:
        raise ValueError("Invalid simulation time or speed")
    settings = state.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Invalid settings object")
    for key, low, high in (("productionRate", 20, 200), ("moistureLimit", 8, 20), ("storageLimit", 18, 40)):
        if not finite(settings.get(key)) or not low <= settings[key] <= high:
            raise ValueError("Invalid settings")
    if 'factory' in state:
        factory = state['factory']
        if not isinstance(factory, dict) or set(factory) != {'stations'} or not isinstance(factory['stations'], dict):
            raise ValueError('Invalid factory configuration')
        for station_id, config in factory['stations'].items():
            if station_id not in STATION_IDS or not isinstance(config, dict) or set(config)-{'capacityKgPerMinute','status'}:
                raise ValueError('Invalid factory station')
            capacity = config.get('capacityKgPerMinute', 1)
            status = config.get('status', 'idle')
            if not finite(capacity) or not 0 < capacity <= 1000000 or not isinstance(status, str) or status not in {'idle','running','hold','down'}:
                raise ValueError('Invalid factory capacity or status')
    ids = set()
    for lot in lots:
        if not isinstance(lot, dict) or not isinstance(lot.get("id"), str) or not lot["id"] or len(lot["id"]) > 80 or lot["id"] in ids:
            raise ValueError("Invalid or duplicate lot ID")
        ids.add(lot["id"])
        if lot.get("stage") not in STAGES or lot.get("status") not in {"ok", "hold", "warning"} or not finite(lot.get("quantity")) or lot["quantity"] <= 0:
            raise ValueError("Invalid lot quantity or status")
        if not finite(lot.get("progress")) or not 0 <= lot["progress"] <= 1:
            raise ValueError("Invalid lot progress")
        if not isinstance(lot.get("product"), str) or len(lot["product"]) > 200 or not isinstance(lot.get("rawId"), str) or len(lot["rawId"]) > 80:
            raise ValueError("Invalid lot text fields")
        qc = lot.get("qc")
        if not isinstance(qc, dict):
            raise ValueError("Missing QC record")
        for field, low, high in (("moisture", 0, 100), ("temperature", -30, 100)):
            v = qc.get(field)
            if v is not None and (not finite(v) or not low <= v <= high):
                raise ValueError("Invalid QC measurement")
    for a in alerts:
        if not isinstance(a, dict) or a.get("lotId") not in ids or not isinstance(a.get("resolved"), bool):
            raise ValueError("Invalid alert linkage")
    for key in ("nextId", "nextEventId", "nextAlertId"):
        if not finite(state.get(key)) or state[key] < 1:
            raise ValueError("Invalid counters")
    if 'orders' in state:
        # A single shared allocation validator prevents browser/server drift. The
        # bounded child process reads only this copied snapshot, with no shell.
        try:
            result=subprocess.run([os.environ.get('GRAINWORKS_NODE','node'),str(ROOT/'tool_bridge.js')],
                input=json.dumps({'operation':'orders_validate','state':state},ensure_ascii=False,allow_nan=False),
                capture_output=True,text=True,encoding='utf-8',timeout=5,cwd=ROOT)
        except (OSError,subprocess.SubprocessError) as exc:
            raise ValueError('Order validation unavailable; snapshot not saved') from exc
        if result.returncode: raise ValueError('Invalid orders: '+result.stderr[:500])
    return state


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.commit()

    def read(self):
        with self.lock, closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload, updated_at FROM state WHERE id=1").fetchone()
        return {"state": json.loads(row[0]) if row else None, "updatedAt": row[1] if row else None}

    def write(self, value):
        payload = json.dumps(validate_state(value), ensure_ascii=False, allow_nan=False)
        with self.lock, closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO state(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP", (payload,))
            db.commit()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, store, agent=None, **kwargs):
        self.store = store
        self.agent = agent
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def respond(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.local_request(): return
        path = urlparse(self.path).path
        if path == "/api/state":
            self.respond(200, self.store.read())
        elif path == "/api/health":
            self.respond(200, {"ok": True, "storage": "sqlite", "synthetic": True})
        elif path == "/api/agent/status":
            self.respond(200, backend_status())
        elif path == "/api/rag/status":
            from rag_backend import rag_status
            self.respond(200, rag_status())
        elif path.startswith('/api/agent/runs/'):
            run=self.agent.get(path.rsplit('/',1)[-1]) if self.agent else None
            self.respond(200 if run else 404, run or {'error':'Run not found'})
        elif path.startswith("/api/"):
            self.respond(404, {"error": "Endpoint not found"})
        elif not self.static_allowed(path):
            self.respond(404, {"error": "File not served"})
        else:
            super().do_GET()

    def do_POST(self):
        if not self.local_request(mutation=True): return
        path=urlparse(self.path).path
        if path != "/api/state" and path != '/api/agent/runs' and path != '/api/documents/extract' and not (path.startswith('/api/agent/proposals/') and path.endswith('/review')):
            self.respond(404, {"error": "Endpoint not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                self.respond(413, {"error": "Body limit exceeded"})
                return
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if path != '/api/state' and not isinstance(value,dict): raise ValueError('Payload must be an object')
            if path == '/api/state':
                self.store.write(value)
                self.respond(200, {'ok':True})
            elif path == '/api/agent/runs':
                if not self.agent: raise RuntimeError('Agent service is not started')
                self.respond(202, {'runId':self.agent.submit(value)})
            elif path == '/api/documents/extract':
                from document_intake import ingest_document, DocumentIntakeError
                try:
                    if not isinstance(value,dict): raise ValueError('Document payload must be an object')
                    result=ingest_document(value.get('data_base64',''),value.get('mime_type',''),value.get('filename',''))
                except DocumentIntakeError as exc:
                    self.respond(400, {'error':str(exc),'code':getattr(exc,'code','document_error')});return
                self.respond(200,result)
            else:
                if not self.agent: raise RuntimeError('Agent service is not started')
                proposal_id=path.split('/')[4]
                self.respond(200,self.agent.review(proposal_id,value.get('decision')))
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self.respond(503, {'error':str(exc)})
        except ImportError as exc:
            self.respond(503, {'error':'Document dependency unavailable: '+str(exc)})

    def local_request(self, mutation=False):
        host=self.headers.get('Host','')
        try:
            parsed=urlparse('http://'+host)
            allowed=parsed.hostname in ['127.0.0.1','localhost','::1'] and parsed.port==self.server.server_port and not parsed.username and not parsed.password
        except ValueError: allowed=False
        if not allowed:
            self.respond(403, {'error':'Localhost Host only'});return False
        origin=self.headers.get('Origin')
        if origin and origin != 'http://'+host:
            self.respond(403, {'error':'Local same-origin requests only'});return False
        if mutation and self.headers.get('Sec-Fetch-Site') in ['cross-site','same-site']:
            self.respond(403, {'error':'Local same-origin requests only'});return False
        return True

    def static_allowed(self, path):
        resolved=Path(self.translate_path(path)).resolve()
        if not resolved.is_relative_to(ROOT): return False
        relative=resolved.relative_to(ROOT).as_posix()
        if relative in ['.','index.html','app.js','engine.js','assets.js','bootstrap.js','scene.js','styles.css','agent.css','agent.js','agent-ui.js','rag-ui.js','rag.css','factory_model.js','factory-ui.js','factory-ui.css','order_model.js','order-ui.js','order-ui.css','sample-lots.csv','README.md']: return True
        if relative.startswith('docs/') and resolved.is_file() and resolved.suffix.lower() in ['.pdf','.png','.jpg','.svg','.md','.json','.mp4']: return True
        return relative.startswith('vendor/') and resolved.is_file() and resolved.suffix in ['.js','.css','.woff','.woff2','.png','.jpg','.svg']

    def do_HEAD(self):
        if not self.local_request(): return
        if not self.static_allowed(urlparse(self.path).path):
            self.respond(404, {'error':'File not served'});return
        super().do_HEAD()


def main():
    parser = argparse.ArgumentParser(description="Run the local Grainworks demo")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--db", type=Path, default=Path.cwd() / "work" / "grainworks.sqlite3")
    args = parser.parse_args()
    store = Store(args.db)
    if store.read()['state'] is None:
        store.write(json.loads((ROOT/'fixtures/factory-state.json').read_text(encoding='utf-8')))
    agent=AgentService(store)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, store=store, agent=agent))
    print(f"Grainworks: http://127.0.0.1:{args.port} | database: {store.path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
