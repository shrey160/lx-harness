import React, {useState} from 'react';
import {api} from './api.js';

const KINDS=[['command','A command on this machine'],['docker','A Docker image'],['url','A server already running at an address']];
const HINTS={
  command:'Runs on this machine, outside the sandbox, with a short environment. For example npx, uvx or python.',
  docker:'Runs by image id with no capabilities, 1 GB and 1 CPU. Pull the image first. The network stays off and folders are shared read-only unless you say otherwise.',
  url:'For a container or service you run yourself, or a remote server. It must speak MCP over streamable HTTP. Use https, or http on this machine.'};
const EMPTY={name:'',kind:'command',command:'',image:'',args:'',network:false,mountSource:'',mountTarget:'',url:'',secretName:'',secretValue:''};

function describe(server){
  return server.kind==='docker'?`${server.image} (${server.image_id.slice(7,19)})${server.network?', network on':''}`:server.kind==='url'?server.url:server.command.join(' ');
}

function ServerRow({server,ui,refresh}){
  const [secret,setSecret]=useState({name:'',value:''});
  const mark=(tool,on)=>{const tools=server.tools.filter(item=>item.tool===tool?on:item.read_only).map(item=>item.tool);ui.act(async()=>{await api(`mcp/${server.id}/read_only`,{tools});await refresh();});};
  const change=action=>ui.act(async()=>{await api(`mcp/${server.id}/${action}`,{});await refresh();});
  const save=()=>ui.act(async()=>{await api(`mcp/${server.id}/secret`,secret);setSecret({name:'',value:''});await refresh();ui.notify('Secret saved, and the server read again.');});
  return <li>
    <div className="provider-row">
      <div><strong>{server.id}</strong><span className="muted"><code>{describe(server)}</code></span></div>
      <span><button className="button" disabled={ui.busy} onClick={()=>change('refresh')}>Read again</button> <button className="button" disabled={ui.busy} onClick={()=>change('remove')}>Remove</button></span>
    </div>
    {server.secrets.length>0&&<p className="muted">Secrets: {server.secrets.join(', ')}</p>}
    <div className="checks">{server.tools.map(tool=><label className="check" key={tool.tool}><input type="checkbox" checked={tool.read_only} disabled={ui.busy} onChange={e=>mark(tool.tool,e.target.checked)}/><span><code>{tool.tool}</code> changes nothing<span className="muted"> {tool.description}</span></span></label>)}</div>
    <div className="key-row">
      <label className="field">Secret name<input value={secret.name} onChange={e=>setSecret(s=>({...s,name:e.target.value}))} placeholder={server.kind==='url'?'TOKEN':'API_KEY'}/></label>
      <label className="field">Value<input type="password" autoComplete="new-password" value={secret.value} onChange={e=>setSecret(s=>({...s,value:e.target.value}))}/></label>
      <button className="button" disabled={ui.busy||!secret.name||!secret.value} onClick={save}>Save secret</button>
    </div>
  </li>;
}

export function McpServers({system,ui,refresh}){
  const [form,setForm]=useState(EMPTY);
  const set=(field,value)=>setForm(f=>({...f,[field]:value}));
  const ready=form.name&&({command:form.command,docker:form.image,url:form.url}[form.kind])&&(!form.mountSource===!form.mountTarget)&&(!form.secretName===!form.secretValue);
  const connect=()=>ui.act(async()=>{
    await api('mcp',{name:form.name,kind:form.kind,command:form.command,image:form.image,args:form.args,network:form.network,url:form.url,
      mounts:form.mountSource?[{source:form.mountSource,target:form.mountTarget}]:[],secrets:form.secretName?{[form.secretName]:form.secretValue}:{}});
    setForm(EMPTY);await refresh();ui.notify('Server connected. Mark the tools that change nothing.');
  });
  return <section>
    <h2>MCP servers</h2>
    <p className="muted">Tools a run's agent can use, and the loop can learn. A run repeats calls many times, so only tools you mark as changing nothing can be chosen, on a workspace's Spec tab. What a server returns can end up in a learned harness, so connect only what you trust. An orchestrator can choose marked tools, but only you can connect a server.</p>
    {system?.mcp_servers.length>0&&<ul className="list">{system.mcp_servers.map(server=><ServerRow key={server.id} server={server} ui={ui} refresh={refresh}/>)}</ul>}
    <h3>Connect a server</h3>
    <div className="key-row">
      <label className="field">Name<input value={form.name} onChange={e=>set('name',e.target.value)} placeholder="prices"/></label>
      <label className="field">Runs as<select value={form.kind} onChange={e=>set('kind',e.target.value)}>{KINDS.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
    </div>
    <p className="muted">{HINTS[form.kind]}</p>
    {form.kind==='command'&&<label className="field">Command<input value={form.command} onChange={e=>set('command',e.target.value)} placeholder="npx -y @acme/prices-mcp"/></label>}
    {form.kind==='docker'&&<>
      <div className="key-row">
        <label className="field">Image<input value={form.image} onChange={e=>set('image',e.target.value)} placeholder="ghcr.io/acme/prices-mcp:1.2"/></label>
        <label className="field">Arguments<input value={form.args} onChange={e=>set('args',e.target.value)} placeholder="optional"/></label>
      </div>
      <label className="check"><input type="checkbox" checked={form.network} onChange={e=>set('network',e.target.checked)}/><span>Let the container use the network</span></label>
      <div className="key-row">
        <label className="field">Share a folder, read-only<input value={form.mountSource} onChange={e=>set('mountSource',e.target.value)} placeholder="C:/data/prices"/></label>
        <label className="field">Path in the container<input value={form.mountTarget} onChange={e=>set('mountTarget',e.target.value)} placeholder="/data"/></label>
      </div>
    </>}
    {form.kind==='url'&&<label className="field">Address<input value={form.url} onChange={e=>set('url',e.target.value)} placeholder="http://localhost:8931/mcp"/></label>}
    <div className="key-row">
      <label className="field">Secret name, if it needs one<input value={form.secretName} onChange={e=>set('secretName',e.target.value)} placeholder={form.kind==='url'?'TOKEN':'API_KEY'}/><small className="muted">{form.kind==='url'?'TOKEN is sent as a bearer token.':'Reaches the server as this environment variable.'}</small></label>
      <label className="field">Value<input type="password" autoComplete="new-password" value={form.secretValue} onChange={e=>set('secretValue',e.target.value)}/></label>
    </div>
    <div className="actions"><button className="button primary" disabled={ui.busy||!ready} onClick={connect}>Connect</button></div>
  </section>;
}
