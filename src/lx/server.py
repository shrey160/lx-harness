"""Loopback-only HTTP API and built React UI for the local app."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse

from .engine import Engine, DEFAULT_ROOT, ROOT

MAX_BODY = 30*1024*1024


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
                    return self.respond({"token":token})
                if path == "/api/state":
                    return self.respond(app.state())
                if path == "/api/system":
                    return self.respond(app.system())
                if path == "/api/demo.zip":
                    import io,zipfile
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
                        for p in (app.demo.root/"worker_inputs").rglob("*"):
                            if p.is_file(): archive.write(p,p.relative_to(app.demo.root/"worker_inputs").as_posix())
                    return self.respond(stream.getvalue(),mime="application/zip")
                pieces = path.strip("/").split("/")
                if len(pieces)==5 and pieces[:2]==["api","packages"] and pieces[4]=="download":
                    return self.respond(app.package_bytes(pieces[2],pieces[3]),mime="application/zip")
                if len(pieces)==5 and pieces[:2]==["api","trajectories"] and pieces[4]=="download":
                    return self.respond(app.trajectory_bytes(pieces[2],pieces[3]),mime="application/zip")
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

        def _read_body(self):
            """The whole body comes off the socket first: closing with unread bytes resets the
            connection on Windows, and the client loses the reply (WinError 10053)."""
            try:
                length = int(self.headers.get("Content-Length","0"))
            except ValueError:
                return None
            return self.rfile.read(length) if 0 < length <= MAX_BODY else None

        def do_POST(self):
            body = self._read_body()
            if not self._valid_host() or self.headers.get("X-LX-Token") != token:
                return self.respond({"error":"Refresh this local session before making changes"},403)
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"}:
                return self.respond({"error":"Cross-origin change rejected"},403)
            if self.headers.get_content_type() != "application/json":
                return self.respond({"error":"Send JSON"},415)
            try:
                if body is None:
                    raise ValueError("Request is empty or too large")
                data = json.loads(body)
                if not isinstance(data,dict):
                    raise ValueError("Send an object")
                route = urlparse(self.path).path.strip("/").split("/")
                if route == ["api","projects"]:
                    blob = base64.b64decode(data.pop("zip_base64"),validate=True) if data.get("source")=="zip" else None
                    result = app.create_project(data,blob)
                elif route == ["api","workspaces","open"]:
                    result = app.open_workspace(data.get("path"))
                elif route == ["api","pick-folder"]:
                    result = app.pick_folder()
                elif route == ["api","models","lookup"]:
                    result = app.provider_models(data.get("base_url"),data.get("api_key"))
                elif route == ["api","models"]:
                    result = app.registry.create(data.get("name"),data.get("model_id"),data.get("capabilities"),data.get("api_key"),
                        base_url=data.get("base_url"),accepts_images=data.get("accepts_images",False),
                        supports_tools=data.get("supports_tools",False),pricing=data.get("pricing"),context_limit=data.get("context_limit"),
                        requests_per_minute=data.get("requests_per_minute"),limits=data.get("limits"))
                elif len(route)==4 and route[:2]==["api","models"]:
                    if route[3]=="update": result = app.registry.update(route[2],name=data.get("name"),capabilities=data.get("capabilities"),api_key=data.get("api_key"),
                        base_url=data.get("base_url"),accepts_images=data.get("accepts_images"),
                        supports_tools=data.get("supports_tools"),pricing=data.get("pricing"),context_limit=data.get("context_limit"),
                        requests_per_minute=data.get("requests_per_minute"),limits=data.get("limits"))
                    elif route[3]=="test": result = app.test_provider(route[2])
                    elif route[3]=="enabled": result = app.registry.set_enabled(route[2],data.get("enabled"))
                    elif route[3]=="delete": app.registry.delete(route[2]); result={"deleted":True}
                    else: raise ValueError("Unknown model action")
                elif len(route)==4 and route[:2]==["api","projects"]:
                    identifier,action = route[2:]
                    if action=="discuss": result=app.discuss(identifier,data.get("text"),data.get("data_approved"),data.get("mode"))
                    elif action=="spec": result=app.edit_spec(identifier,data)
                    elif action=="budget": result=app.set_budget(identifier,data.get("cap_usd"))
                    elif action=="task": result=app.set_task(identifier,data.get("task"),data.get("check"))
                    elif action=="check-test": result=app.test_check(identifier)
                    elif action=="approve": result=app.approve(identifier,data)
                    elif action=="run": result=app.start(identifier)
                    else: raise ValueError("Unknown project action")
                elif len(route)==5 and route[:2]==["api","jobs"]:
                    identifier,job_id,action = route[2:]
                    if action=="stop": result=app.stop(identifier,job_id)
                    elif action=="resume": result=app.resume(identifier,job_id)
                    elif action=="fresh": result=app.fresh(identifier,job_id)
                    elif action=="regrade": result=app.regrade(identifier,job_id)
                    elif action=="release": result=app.release_held(identifier,job_id,data.get("charged"))
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
    server=server_for(Engine(args.root),args.port)
    print(f"LX: http://127.0.0.1:{server.server_port}",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
