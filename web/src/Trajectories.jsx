import React, {useEffect, useState} from 'react';
import {active, api, plural} from './api.js';

const STAGES={base1:'Base harness',inv1:'How the agent works',dom1:'The subject',c1:'Combined'};
const name=path=>{
  const parts=path.split('/'),last=parts[parts.length-1];
  const stage=STAGES[last]||(last==='trial'?'Trial':last);
  return parts.length>=3?`${parts[parts.length-2]} · ${stage}`:stage;
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
    <p className="muted">One entry for each agent run. A run's steps are saved when that agent run ends, so a running job shows the finished ones. The opening prompt, the harness files and the images themselves are not part of the saved trace.</p>
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
    <summary>{name(item.path)} <span className="muted">· {plural(item.turns,'turn')} · {calls}{item.stopped?` · stopped: ${item.stopped}`:item.answered?'':' · no answer'}</span></summary>
    {problem&&<p className="callout bad">{problem}</p>}
    {!record&&!problem&&<p className="muted">Loading…</p>}
    {record&&<ol className="steps">{record.trace.map((step,i)=><Step key={i} step={step}/>)}
      <li className="step final"><strong>Final reply</strong>{record.final_text?<pre className="setup"><code>{record.final_text}</code></pre>:<p className="muted">None: the agent run ended without an answer.</p>}</li>
    </ol>}
  </details>;
}

function Block({text}){
  return <details open={text.length<=SHORT}><summary className="muted">{text.length<=SHORT?'Shown':`${text.length.toLocaleString()} characters`}</summary><pre className="setup"><code>{text}</code></pre></details>;
}

function Step({step}){
  if(step.type==='tools')return <li className="step meta"><strong>Tools available</strong> <span className="muted">{step.names.join(', ')||'none'}</span></li>;
  if(step.type==='stopped')return <li className="step"><p className="callout warn">Stopped: {step.reason}</p></li>;
  if(step.type==='assistant')return <li className="step agent">
    <strong>Agent</strong> <span className="muted">{step.output_tokens!=null?`${step.output_tokens.toLocaleString()} output tokens`:''}</span>
    {step.content&&<p className="said">{step.content}</p>}
    {step.reasoning&&<details><summary className="muted">Reasoning notes (kept to a short excerpt)</summary><p className="said">{step.reasoning}</p></details>}
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
