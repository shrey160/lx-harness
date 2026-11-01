"""A small stdio MCP server for the tests: a price list, plus ways for a server to misbehave."""
import json
import sys

PRICES = {"cup": 4.5, "bowl": 6.25}
LOOKUP = {"name": "price.lookup", "description": "Unit price of an item.",
          "inputSchema": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}}
WRITE = {"name": "orders_delete", "description": "Delete an order.", "inputSchema": {"type": "object", "properties": {}}}
calls = 0


def result(identifier, value):
    print(json.dumps({"jsonrpc": "2.0", "id": identifier, "result": value}), flush=True)


def call(arguments):
    global calls
    calls += 1
    item = arguments.get("item")
    if item == "huge":
        return {"content": [{"type": "text", "text": "x"*20000}]}
    if item == "picture":
        return {"content": [{"type": "image", "data": "AAAA", "mimeType": "image/png"}, {"type": "text", "text": "beside it"}]}
    if item == "sleep":
        __import__("time").sleep(60)
    if item not in PRICES:
        return {"isError": True, "content": [{"type": "text", "text": f"no such item: {item}"}]}
    return {"content": [{"type": "text", "text": f"{PRICES[item]} (call {calls})"}]}


def main():
    flags = set(sys.argv[1:])
    if "--env" in flags:
        LOOKUP["description"] = " ".join(sorted(key for key in __import__("os").environ if "LX_TEST" in key)) or "clean"
    if "--changed" in flags:
        LOOKUP["description"] = "Something else now."
    print("a stray log line", flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        identifier, method = message.get("id"), message.get("method")
        if method == "initialize":
            result(identifier, {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}})
            print(json.dumps({"jsonrpc": "2.0", "id": "ask", "method": "roots/list"}), flush=True)
        elif method == "tools/list":
            result(identifier, {"tools": [LOOKUP, WRITE]})
        elif method == "tools/call":
            if message["params"]["name"] == "price.lookup":
                result(identifier, call(message["params"]["arguments"]))
            else:
                result(identifier, {"content": [{"type": "text", "text": "deleted"}]})


if __name__ == "__main__":
    main()
