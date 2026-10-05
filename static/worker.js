// Standalone (phone) engine: runs the game's own Python code in the browser
// with Pyodide, on a background thread so the table stays smooth while the
// AI thinks. The page sends {id, name, body}; we answer with the same JSON the
// desktop server returns from /api/<name>.
"use strict";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const FILES = ["__init__.py", "cards.py", "engine.py", "bots.py", "mc.py", "match.py", "session.py"];

importScripts(PYODIDE + "pyodide.js");

const ready = (async () => {
  postMessage({ progress: "Loading Python..." });
  const py = await loadPyodide({ indexURL: PYODIDE });
  postMessage({ progress: "Loading the game..." });
  py.FS.mkdirTree("/home/pyodide/dehla");
  for (const f of FILES) {
    const r = await fetch("py/dehla/" + f, { cache: "no-cache" });
    if (!r.ok) throw new Error("missing " + f);
    py.FS.writeFile("/home/pyodide/dehla/" + f, await r.text());
  }
  py.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from dehla.engine import IllegalMove
from dehla.session import Session
SESSION = Session(think_time=1.5)

def handle(request_json):
    r = json.loads(request_json)
    try:
        return json.dumps({"ok": True, "result": SESSION.handle(r["name"], r["body"])})
    except IllegalMove as e:
        return json.dumps({"ok": False, "error": str(e)})
`);
  postMessage({ progress: null });
  return py.globals.get("handle");
})();

onmessage = async (e) => {
  const { id, name, body } = e.data;
  try {
    const handle = await ready;
    const out = JSON.parse(handle(JSON.stringify({ name, body: body || {} })));
    postMessage({ id, ...out });
  } catch (err) {
    postMessage({ id, ok: false, error: String(err && err.message || err) });
  }
};
