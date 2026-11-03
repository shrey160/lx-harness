import React, {useEffect, useState} from 'react';
import {active, api, plural} from './api.js';

const STAGES={base1:'Base harness',inv1:'How the agent works',dom1:'The subject',c1:'Combined'};
export const trajectoryName=item=>{
  const path=item.path;
  const parts=path.split('/'),last=parts[parts.length-1];
  const stage=STAGES[last]||(last==='trial'?'Trial':last);
  return item.sample?`${item.sample} · ${stage}`:parts.length>=3?`${parts[parts.length-2]} · ${stage}`:stage;
};
const pretty=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
const SHORT=400;

export function Trajectories({project,job}){
  const [list,setList]=useState(null),running=active(job);
  useEffect(()=>{
    const load=()=>api(`trajectories/${project.id}/${job.id}`).then(setList,()=>{});
    load();
    if(!running)return undefined;
    const timer=setInterval(load,5000);
    return ()=>clearInterval(timer);
  },[job.id,running]);
  if(!list)return null;
  return <div className="trajectories">
    <p className="muted">One entry for each agent run. A run's steps are saved when that agent run ends, so a running job shows the finished ones. Opening messages are shown where the run kept them. The images themselves are not saved.</p>
    {list.executions.length===0&&<p className="muted">{running?'Nothing finished yet.':'This run left no trajectories.'}</p>}
    {list.executions.map(item=><Execution key={item.path} project={project} job={job} item={item}/>)}
  </div>;
}

function Execution({project,job,item}){
  const [record,setRecord]=useState(null),[problem,setProblem]=useState('');
  const load=open=>{
    if(open&&!record&&!problem)api(`trajectories/${project.id}/${job.id}/trace?path=${encodeURIComponent(item.path)}`).then(setRecord,e=>setProblem(e.message));
  };
  const calls=Object.entries(item.tool_calls).map(([tool,count])=>`${tool} ${count}`).join(', ')||'no tool calls';
  return <details className="execution" onToggle={e=>load(e.currentTarget.open)}>
    <summary>{trajectoryName(item)} <span className="muted">· {plural(item.turns,'turn')} · {calls}{item.stopped?` · stopped: ${item.stopped}`:!item.answered?' · no answer':item.reply==='text'?' · reply not JSON':''}</span></summary>
    {item.sample&&item.internal_sample!==item.sample&&<p className="muted">Sample folder: {item.sample}. The saved messages use execution ID {item.internal_sample}.</p>}
    {problem&&<p className="callout bad">{problem}</p>}
    {!record&&!problem&&<p className="muted">Loading…</p>}
    {record&&<ol className="steps">
      {record.opening?record.opening.map((message,i)=><li className="step meta" key={'open'+i}><strong>{message.role==='developer'?'Instructions given to the agent':message.role==='user'?'Request given to the agent':message.role}</strong><Block text={message.content} closed/></li>)
        :<li className="step meta"><span className="muted">The opening messages were not kept for this run.</span></li>}
      {record.trace.map((step,i)=><Step key={i} step={step}/>)}
      <li className="step final"><strong>Final reply</strong>{record.final_text?<pre className="setup"><code>{record.final_text}</code></pre>:<p className="muted">None: the agent run ended without an answer.</p>}</li>
    </ol>}
  </details>;
}

function Block({text,closed}){
  return <details open={!closed&&text.length<=SHORT}><summary className="muted">{!closed&&text.length<=SHORT?'Shown':`${text.length.toLocaleString()} characters`}</summary><pre className="setup"><code>{text}</code></pre></details>;
}

function Step({step}){
  if(step.type==='tools')return <li className="step meta"><strong>Tools available</strong> <span className="muted">{step.names.join(', ')||'none'}</span></li>;
  if(step.type==='stopped')return <li className="step"><p className="callout warn">Stopped: {step.reason}</p></li>;
  if(step.type==='assistant')return <li className="step agent">
    <strong>Agent</strong> <span className="muted">{step.output_tokens!=null?`${step.output_tokens.toLocaleString()} output tokens`:''}</span>
    {step.content&&<p className="said">{step.content}</p>}
    {step.reasoning&&<details><summary className="muted">Saved reasoning (older runs may contain an excerpt)</summary><p className="said">{step.reasoning}</p></details>}
    {step.tool_calls.map((call,i)=><div className="call" key={i}><code>{call.name}</code> <code className="muted">{JSON.stringify(call.arguments)}</code></div>)}
  </li>;
  if(step.type==='tool'){
    const bad='error' in step;
    return <li className={'step tool'+(bad?' bad':'')}>
      <strong>{bad?'Tool error':'Result'}</strong> <code>{step.name}</code> <code className="muted">{JSON.stringify(step.arguments)}</code>
      <Block text={pretty(bad?step.error:step.result)}/>
    </li>;
  }
  return <li className="step meta"><span className="muted">{step.type}</span></li>;
}
