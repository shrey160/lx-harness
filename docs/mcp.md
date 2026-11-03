# MCP tools

**Connecting an external agent to operate LX?** Use [Agent-facing MCP](agent-mcp.md).
This guide covers tools LX workers use from other servers, configured under
Settings → MCP servers. These are two separate connections.

A run's agent can use tools from an MCP server, and the loop learns how to use them from
its runs, as with any other tool. Connecting a coding agent to LX is separate and works
the other way round: there, LX is the server.

## How a server can be hosted

| Case | How to connect it | What to know |
|---|---|---|
| A package, such as `npx` or `uvx` | Settings, MCP servers, "A command on this machine", or `lx mcp connect prices -- npx -y @acme/prices-mcp` | Runs outside the sandbox with a short environment. A first `npx` download counts against the 30-second start, so install it first |
| A script of your own | `lx mcp connect prices -- python server.py` | The same |
| A Docker image LX starts for each run | "A Docker image", or `lx mcp connect prices --image ghcr.io/acme/prices-mcp:1.2` | Pull it first. LX runs it by image id with no capabilities, 1 GB, 1 CPU and 256 processes |
| A Docker image that needs the internet | Add `--network` | The network stays off unless you say so |
| A Docker image that reads your files | Add `--mount C:/data/prices:/data` | Shared read-only. Files outside stay out |
| A container or service you keep running, such as with Compose | "A server already running at an address", or `lx mcp connect prices --url http://localhost:8931/mcp` | It must speak streamable HTTP. LX neither starts nor stops it, so it must be up for the whole run |
| A remote server | `lx mcp connect prices --url https://mcp.example.com/mcp` | The agent's tool arguments leave this machine, and the data notice says where |

Use a Docker image for a server you did not write. A command has the run of your
account, and a container does not.

**Not supported yet:** OAuth sign-in, the older HTTP with server-sent events, and a server
with only that transport. Use a bearer token, or a server that speaks stdio. Tools that
change anything are also out, on purpose.

## Secrets

Give a server its keys in Settings or with `--secret NAME`, which asks for the value.
They use the operating system's keychain (Windows Credential Manager on Windows),
with restricted file storage on Linux without a usable keychain; see
[configuration](configuration.md). They do not enter a workspace, spec or package,
and are not returned through LX's agent tools.

- A command gets each one as an environment variable.
- A container gets it the same way. The value is not on Docker's command line.
- A URL server gets the secret called `TOKEN` as a bearer token.

A server gets only its own secrets, and none of the keys LX holds. To change one, use
`lx mcp secret prices API_KEY`. LX reads the server again, so a wrong value shows at once.

## Read-only, and who may do what

A run repeats calls many times: the last three audit runs made over 1,200 tool calls on
edited harnesses. A tool that writes would fire hundreds of times. So nothing can be chosen
until a person marks it read-only, with the checkbox or `lx mcp read-only prices lookup`.
MCP's own read-only labels are not trusted, because a server writes them.

| Who | Can do |
|---|---|
| A person | Connect, mark, refresh, remove, set secrets |
| An orchestrator | See marked and unmarked tools with `environment`, and choose marked ones with `spec_draft`. It never sees a command, image, address or secret |

An orchestrator cannot connect a server, because that would let it run any program here.

## Pins

Connecting pins each tool's text, and what the server runs from: the command, the image id
or the address. A spec holds what it approved.

- A live run reads the tools first and refuses to start if they differ.
- `lx mcp refresh prices` or "Read again" reads them on purpose. A tool whose text changed
  needs marking again. If an image's tag now points to a new image, every tool does.
- After a change, write a new spec version.

## During a run

- The controller holds the connection, so the sandbox stays without network. A server
  starts when the run needs it and stops when the run ends. A trial works the same way.
- A run allows 500 MCP calls by default, which you can change with the run limits. Each
  call has 30 seconds.
- A repeat of the same call comes from a cache, so reruns compare harnesses and not a
  server whose answers moved.
- The agent gets text only, up to 8,000 characters. A note replaces images and other
  content.
- Helper scripts receive results as `mcp_results`. They cannot call tools.
- Previews answer from doubles and start no server.

## What the loop learns

Nothing about a tool is written into the harness: the agent gets its name and description.
The improver reads how the agent used it and writes skills and tool guidance, as it does
for SAM. So write clear descriptions on your server, and do not put procedure in the task
text.

A package names the servers it needs in `limitations.md`, and a learned harness only works
with them connected.

## Risks

- What a server returns can end up in a learned rule. Read `learned.md` before you use a
  package from a server you do not control.
- A server can hand the agent the answers, which makes scores mean nothing. Keep answers
  away from any server a task can reach.
- Scores with different servers or tools are not comparable.
