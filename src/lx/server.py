"""Loopback-only HTTP API and built React UI for the local controlled preview."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse

from .app import Application, DEFAULT_ROOT, ROOT


def server_for(app, port=8765):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def respond(self,value,status=200,mime="application/json"):
            body = value if isinstance(value,bytes) else json.dumps(value,allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type",mime)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; img-src 'self' data:; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _valid_host(self):
            return self.headers.get("Host","").split(":")[0] in {"127.0.0.1","localhost"}

        def do_GET(self):
            if not self._valid_host():
                return self.respond({"error":"Invalid host"},403)
            path = urlparse(self.path).path
            try:
                if path == "/api/session":
                    return self.respond({"token":token,"mode":"controlled_preview"})
                if path == "/api/state":
                    return self.respond(app.state())
                if path == "/api/live-proof":
                    return self.respond(json.loads((ROOT/"evidence"/"2026-10-28-live-gate"/"live-report.json").read_text()))
                if path == "/api/demo.zip":
                    import io,zipfile
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
                        for p in (app.demo.root/"worker_inputs").rglob("*"):
                            if p.is_file(): archive.write(p,p.relative_to(app.demo.root/"worker_inputs").as_posix())
                    return self.respond(stream.getvalue(),mime="application/zip")
                pieces = path.strip("/").split("/")
                if len(pieces)==4 and pieces[:2]==["api","packages"] and pieces[3]=="download":
                    return self.respond(app.package_bytes(pieces[2]),mime="application/zip")
                allowed = {"/":"index.html","/app.js":"app.js","/app.css":"app.css"}
                if path not in allowed:
                    return self.respond({"error":"Not found"},404)
                target = ROOT/"web"/"dist"/allowed[path]
                if not target.is_file():
                    return self.respond({"error":"Build the React UI with npm run build in web/"},503)
                mime = "text/html; charset=utf-8" if path=="/" else "text/css" if path.endswith(".css") else "text/javascript"
                return self.respond(target.read_bytes(),mime=mime)
            except ValueError as exc:
                return self.respond({"error":str(exc)},400)
            except Exception:
                return self.respond({"error":"Request failed; private details withheld"},500)

        def do_POST(self):
            if not self._valid_host() or self.headers.get("X-LX-Token") != token:
                return self.respond({"error":"Refresh this local session before making changes"},403)
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"}:
                return self.respond({"error":"Cross-origin change rejected"},403)
            if self.headers.get_content_type() != "application/json":
                return self.respond({"error":"Send JSON"},415)
            try:
                length = int(self.headers.get("Content-Length","0"))
                if not 0 < length <= 30*1024*1024:
                    raise ValueError("Request is empty or too large")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError("Send an object")
                route = urlparse(self.path).path.strip("/").split("/")
                if route == ["api","projects"]:
                    blob = base64.b64decode(data.pop("zip_base64"),validate=True) if data.get("source")=="zip" else None
                    result = app.create_project(data,blob)
                elif route == ["api","models"]:
                    result = app.registry.create(data.get("name"),data.get("model_id"),data.get("capabilities"),data.get("api_key"))
                elif len(route)==4 and route[:2]==["api","models"]:
                    if route[3]=="update": result = app.registry.update(route[2],name=data.get("name"),capabilities=data.get("capabilities"),api_key=data.get("api_key"))
                    elif route[3]=="delete": app.registry.delete(route[2]); result={"deleted":True}
                    else: raise ValueError("Unknown model action")
                elif len(route)==4 and route[:2]==["api","projects"]:
                    identifier,action = route[2:]
                    if action=="discuss": result=app.discuss(identifier,data.get("text"),data.get("data_approved"),data.get("mode"))
                    elif action=="spec": result=app.edit_spec(identifier,data)
                    elif action=="approve": result=app.approve(identifier,data)
                    elif action=="run": result=app.start(identifier)
                    else: raise ValueError("Unknown project action")
                elif len(route)==4 and route[:2]==["api","jobs"]:
                    identifier,action = route[2:]
                    if action=="stop": result=app.stop(identifier)
                    elif action=="resume": result=app.resume(identifier)
                    elif action=="fresh": result=app.fresh(identifier)
                    else: raise ValueError("Unknown job action")
                else:
                    raise ValueError("Unknown API route")
                return self.respond(result)
            except (ValueError,KeyError) as exc:
                return self.respond({"error":str(exc) if isinstance(exc,ValueError) else "Required field missing"},400)
            except Exception:
                return self.respond({"error":"Request failed; private details withheld"},500)

    return ThreadingHTTPServer(("127.0.0.1",port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--root",type=Path,default=DEFAULT_ROOT)
    args=parser.parse_args()
    server=server_for(Application(args.root),args.port)
    print(f"LX controlled preview: http://127.0.0.1:{server.server_port}",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
