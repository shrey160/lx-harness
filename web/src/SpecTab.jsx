import React, {useEffect, useRef, useState} from 'react';
import {api, money} from './api.js';

const ROLES=['orchestrator','improver','worker'];

export function SpecTab({state,project,ui,setTab}){
  const {draft}=project;
  const [goal,setGoal]=useState(draft.goal),[mode,setMode]=useState(draft.mode||'controlled_preview'),[roles,setRoles]=useState(draft.roles);
  const [budget,setBudget]=useState(String(draft.budget_usd)),[workspaceBudget,setWorkspaceBudget]=useState(String(project.budget.cap_usd));
  const [limits,setLimits]=useState(draft.limits),[tools,setTools]=useState(draft.tools),[mcpTools,setMcpTools]=useState((draft.mcp_tools||[]).map(entry=>`${entry.server}/${entry.tool}`)),[reviewing,setReviewing]=useState(false),[checkResult,setCheckResult]=useState(null),[readyResult,setReadyResult]=useState(null);
  const [hintAll,setHintAll]=useState((draft.hints&&draft.hints.all)||''),[hintSamples,setHintSamples]=useState((draft.hints&&draft.hints.samples)||{});
  const folders=[...new Set((project.inventory.inventory||[]).map(item=>item.path.split('/')[0]).filter(name=>/^sample-/.test(name)))].sort();
  const hintsDirty=hintAll!==((draft.hints&&draft.hints.all)||'')||JSON.stringify(hintSamples)!==JSON.stringify((draft.hints&&draft.hints.samples)||{});
  const specDirty=goal!==draft.goal||mode!==(draft.mode||'controlled_preview')||JSON.stringify(roles)!==JSON.stringify(draft.roles)||JSON.stringify(limits)!==JSON.stringify(draft.limits)||JSON.stringify(mcpTools)!==JSON.stringify((draft.mcp_tools||[]).map(entry=>`${entry.server}/${entry.tool}`))||JSON.stringify(tools)!==JSON.stringify(draft.tools)||Number(budget)!==Number(draft.budget_usd)||hintsDirty;
  const [trialCap,setTrialCap]=useState(String(project.trial_cap_usd));
  const trialDirty=Number(trialCap)!==project.trial_cap_usd;
  const budgetDirty=Number(workspaceBudget)!==project.budget.cap_usd||trialDirty;
  const ownLimits=()=>({...(draft.limits_set||{}),...Object.fromEntries(Object.entries(limits).filter(([name,value])=>value!==draft.limits[name]))});
  const save=()=>ui.act(async()=>{
    if(Number(workspaceBudget)!==project.budget.cap_usd)await api(`projects/${project.id}/budget`,{cap_usd:Number(workspaceBudget)});
    if(trialDirty)await api(`projects/${project.id}/trial-cap`,{cap_usd:Number(trialCap)});
    if(specDirty)await api(`projects/${project.id}/spec`,{goal,budget_usd:Number(budget),mode,roles,limits:ownLimits(),...(tools?{tools}:{}),...(draft.task?{mcp_tools:mcpTools,hints:{all:hintAll,samples:hintSamples}}:{})});
    ui.notify(specDirty?'Saved as a new version.':'Budget saved.');
  });
  const testCheck=()=>ui.act(async()=>{setCheckResult(null);setCheckResult(await api(`projects/${project.id}/check-test`,{}));});
  const checkReady=()=>ui.act(async()=>{setReadyResult(null);setReadyResult(await api(`projects/${project.id}/ready`,{}));});
  const hosts=ROLES.map(role=>{const model=state.models.find(m=>m.id===roles[role]);return model?new URL(model.base_url).host:null;});
  return <section className="narrow spec">
    <div className="spec-status"><span className="muted">Version {draft.version}</span>{project.approved&&!specDirty&&<span className="chip ok">Approved</span>}</div>
    <label className="field">Goal<textarea rows="3" value={goal} onChange={e=>setGoal(e.target.value)}/></label>

    <h2>Task</h2>
    {draft.task?<>
      <p className="capital-first">{draft.task.name.replace(/^lx_/,'').replaceAll('_',' ')} · {draft.task.stream_samples} samples in the loop · check <code>{draft.check.sha256.slice(0,12)}</code></p>
      <TaskText project={project}/>
      <div className="actions"><button className="button" disabled={ui.busy} onClick={testCheck}>Test the check</button><button className="button" disabled={ui.busy} onClick={checkReady}>Check these samples</button></div>
      {checkResult&&<CheckScores result={checkResult}/>}
      {readyResult&&<Ready result={readyResult}/>}
    </>:<p className="muted">Image annotation demo. Answers need each object's label and box, within one pixel, and the exact transcript.</p>}

    <h2>Run mode</h2>
    <label className="field"><select aria-label="Run mode" value={mode} onChange={e=>setMode(e.target.value)}><option value="controlled_preview">Preview: scripted replies, no live calls</option><option value="live">Live: real provider calls</option></select></label>

    <h2>Roles</h2>
    <div className="three">{ROLES.map((role,i)=><label className="field" key={role}><span className="capital">{role}</span>
      <select aria-label={role+' provider'} value={roles[role]||''} onChange={e=>setRoles({...roles,[role]:e.target.value})}>{roles[role]==='external-agent'&&<option value="external-agent">Connected agent</option>}{state.models.filter(m=>m.role_ready).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select>
      <small className="muted">{hosts[i]}</small></label>)}</div>
    {mode==='live'&&project.free_providers.length>0&&<p className="callout warn" role="status">No spending limit on {project.free_providers.join(', ')}: it is free or publishes no price, so the budget cannot count it and only the call limits apply.</p>}
    {!specDirty&&project.blocked&&<p className="callout bad" role="status">{project.blocked}</p>}

    {project.tool_choices.length>0&&<>
      <h2>Model tools</h2>
      <div className="checks">{project.tool_choices.map(tool=><label className="check" key={tool.id}><input type="checkbox" checked={(tools||[]).includes(tool.id)} disabled={tool.required||!tool.enabled} onChange={e=>setTools(project.tool_choices.map(c=>c.id).filter(id=>id===tool.id?e.target.checked:(tools||[]).includes(id)))}/>{tool.name}{tool.required?' (this task needs it)':tool.enabled?'':' (off in Settings)'}</label>)}</div>
    </>}

    {project.mcp_choices.length>0&&<>
      <h2>MCP tools</h2>
      <p className="muted">Live runs only: a preview answers from scripted replies and starts no server.</p>
      <div className="checks">{project.mcp_choices.map(tool=><label className="check" key={tool.id}><input type="checkbox" checked={mcpTools.includes(tool.id)} onChange={e=>setMcpTools(chosen=>e.target.checked?[...chosen,tool.id].sort():chosen.filter(item=>item!==tool.id))}/><span><code>{tool.id}</code><span className="muted"> {tool.description}</span></span></label>)}</div>
    </>}

    {draft.task&&<>
      <h2>Hints for the improver <span className="muted">(optional)</span></h2>
      <p className="muted">Notes on how this kind of task is done: the order of work, which source wins over which, what to watch for. Only the improver reads them; the worker and the check never do. Do not write answers, counts, totals or boxes.</p>
      {project.hint_drift&&project.hint_drift.length>0&&<div className="callout warn" role="status"><p>{project.hint_drift.join('. ')}. Approval waits until they match.</p><button className="button" disabled={ui.busy} onClick={()=>ui.act(async()=>{await api(`projects/${project.id}/spec`,{goal:draft.goal,budget_usd:Number(draft.budget_usd),take_hint_files:true,limits:draft.limits_set||{}});ui.notify('Took the hint files.');})}>Use the hint files</button></div>}
      <label className="field">For every sample<textarea rows="5" aria-label="Hints for every sample" maxLength="8000" value={hintAll} onChange={e=>setHintAll(e.target.value)}/></label>
      {folders.length>0&&<details><summary className="muted">Hints for one sample</summary>{folders.map(folder=><label className="field" key={folder}>{folder}<textarea rows="3" aria-label={'Hints for '+folder} maxLength="8000" value={hintSamples[folder]||''} onChange={e=>setHintSamples({...hintSamples,[folder]:e.target.value})}/></label>)}</details>}
    </>}

    <h2>Budget</h2>
    <div className="three two-up">
      <label className="field">This run (USD)<input type="number" min="0.11" max="20" step="0.01" value={budget} onChange={e=>setBudget(e.target.value)}/></label>
      <label className="field">Whole workspace (USD)<input type="number" min="0.11" max="20" step="0.01" value={workspaceBudget} onChange={e=>setWorkspaceBudget(e.target.value)}/><small className="muted">{money(project.budget.spent_usd)} spent, {money(project.budget.reserved_usd)} held</small></label>
      {draft.task&&<label className="field">One trial (USD)<input type="number" min="0.11" max="20" step="0.01" value={trialCap} onChange={e=>setTrialCap(e.target.value)}/><small className="muted">Never more than this run's budget</small></label>}
    </div>

    <Limits fields={state.limit_fields.filter(field=>!(project.hidden_limits||[]).includes(field.name))} values={limits} defaults={project.limit_defaults} onChange={(name,value)=>setLimits({...limits,[name]:value})}/>

    <div className="action-bar">
      <button className="button" disabled={ui.busy||!(specDirty||budgetDirty)} onClick={save}>Save</button>
      <button className="button primary" disabled={ui.busy||specDirty} onClick={()=>setReviewing(true)}>{project.approved?'Review approval':'Review and approve'}</button>
      {specDirty&&<span className="muted" role="status">Save first: approval covers a saved version.</span>}
    </div>
    {reviewing&&<Review project={project} jobs={state.jobs} limitFields={state.limit_fields} ui={ui} close={()=>setReviewing(false)} done={()=>setTab('runs')}/>}
    {project.external_orchestrator&&<Grant project={project} ui={ui}/>}
  </section>;
}

const TASK_TEXT=[['contract','Task text and reply format'],['yardstick','What a good result is'],['selection_checks','Checks before an answer is final']];

function TaskText({project}){
  const {task}=project.draft,[check,setCheck]=useState(null),[problem,setProblem]=useState('');
  const loadCheck=open=>{if(open&&check===null&&!problem)api(`check/${project.id}`).then(reply=>setCheck(reply.source),e=>setProblem(e.message));};
  return <div className="task-text">
    {TASK_TEXT.filter(([field])=>task[field]).map(([field,label])=><details key={field} open={field==='contract'}><summary>{label}</summary><pre className="setup"><code>{task[field]}</code></pre></details>)}
    <details onToggle={e=>loadCheck(e.currentTarget.open)}><summary>Scoring check</summary>
      {problem?<p className="callout bad">{problem}</p>:check===null?<p className="muted">Loading…</p>:<pre className="setup check-source"><code>{check}</code></pre>}
    </details>
    <p className="muted">To change these, ask a connected agent or use Discuss. Each change is a new version and needs a new approval.</p>
  </div>;
}

function Grant({project,ui}){
  const {draft,grant}=project,[cap,setCap]=useState(String(draft.budget_usd)),[days,setDays]=useState('7'),[runs,setRuns]=useState('3'),[granting,setGranting]=useState(false);
  const active=grant&&!grant.revoked&&grant.expires_at*1000>Date.now();
  const usesSpark=Object.values(draft.roles).includes('spark-contributor');
  const give=()=>ui.act(async()=>{await api(`projects/${project.id}/grant`,{cap_usd:Number(cap),days:Number(days),runs:Number(runs),data_approved:true,contributor_accepted:usesSpark,notice_accepted:true});setGranting(false);ui.notify('Grant saved.');});
  const revoke=()=>ui.act(async()=>{await api(`projects/${project.id}/grant-revoke`,{});ui.notify('Grant revoked. A run already going keeps going until you stop it.');});
  return <>
    <h2>Let the agent start live runs</h2>
    {active?<>
      <p>{money(grant.spent_usd)} of {money(grant.cap_usd)} used · {grant.runs_used} of {grant.runs} runs · ends {new Date(grant.expires_at*1000).toLocaleDateString()}</p>
      <div className="actions"><button className="button" disabled={ui.busy} onClick={revoke}>Revoke</button></div>
    </>:<>
      <p className="muted">{grant?(grant.revoked?'The last grant was revoked. ':'The last grant has ended. '):''}A connected agent can approve a live run itself. A grant adds a spending cap, an expiry and a run count for these samples, providers and tools, for an agent you tell to work under it. No person reviews its runs, and a weak check still waits for you.</p>
      {!granting?<div className="actions"><button className="button" disabled={ui.busy} onClick={()=>setGranting(true)}>Set up a grant</button></div>:<>
        <div className="three">
          <label className="field">Spending cap (USD)<input type="number" min="0.11" max={project.budget.remaining_usd} step="0.01" value={cap} onChange={e=>setCap(e.target.value)}/><small className="muted">{money(project.budget.remaining_usd)} left in this workspace</small></label>
          <label className="field">Days<input type="number" min="1" max="30" step="1" value={days} onChange={e=>setDays(e.target.value)}/></label>
          <label className="field">Runs<input type="number" min="1" max="20" step="1" value={runs} onChange={e=>setRuns(e.target.value)}/></label>
        </div>
        <h3>Who receives what</h3>
        <ul className="plain">{project.notice.map(entry=><li key={entry.who}><strong>{entry.who}</strong> ({entry.host}): {entry.gets}</li>)}</ul>
        {usesSpark&&<p className="callout warn">Spark Contributor may use approved content for training.</p>}
        <div className="actions"><button className="button" onClick={()=>setGranting(false)}>Cancel</button><button className="button primary" disabled={ui.busy} onClick={give}>Accept and grant</button></div>
      </>}
    </>}
  </>;
}

function CheckScores({result}){
  const score=value=>value===null?'not tried':value.toFixed(2);
  return <div role="status">
    <p className="muted">Passed {result.cases} cases on {result.samples} samples. A correct answer scores 1.00, a blank one at most {score(result.blank_score)}, another sample's answer at most {score(result.wrong_score)}.</p>
    {result.weaknesses.map(text=><p className="callout warn" key={text}>{text}</p>)}
  </div>;
}

function Ready({result}){
  return <div role="status">
    {result.ready?<p className="muted">These samples can run live. This says the run can start, not how well it will do.</p>:<p className="callout bad">Not ready for a live run yet.</p>}
    {result.blocked&&<p className="callout bad">{result.blocked}</p>}
    {result.problems.length>0&&<ul className="plain">{result.problems.map((item,i)=><li key={i}>{item.sample&&<strong>{item.sample}: </strong>}{item.problem}</li>)}</ul>}
    {result.check&&<CheckScores result={result.check}/>}
  </div>;
}

function Limits({fields,values,defaults,onChange}){
  const changed=fields.filter(field=>values[field.name]!==defaults[field.name]).length;
  return <details className="limits"><summary>Advanced: run limits{changed?` (${changed} changed)`:''}</summary>
    <div className="limit-grid">{fields.map(field=><label className="field" key={field.name}>{field.label}<input aria-label={field.label} type="number" min={field.min} max={field.max} step="1" value={values[field.name]??''} placeholder={field.max==null?'No limit':undefined} onChange={e=>onChange(field.name,e.target.value===''&&field.max==null?null:Number(e.target.value))}/><small className="muted">Default {defaults[field.name]??'no limit'} · {field.max==null?`${field.min} or more; leave empty for no limit`:`${field.min} to ${field.max}`}</small></label>)}</div>
    <p className="muted">A run keeps the limits it was approved with, so a raised limit needs a new version and a new run.</p>
  </details>;
}

function Spending({project,jobs,limitFields}){
  const {draft}=project,worker=draft.providers.worker.name;
  const past=jobs.filter(job=>job.project_id===project.id&&job.mode==='live'&&job.kind!=='trial'&&job.status==='completed'&&job.spec.models.worker===worker).map(job=>job.usage.spent_usd);
  const open=limitFields.filter(field=>field.max==null&&!(project.hidden_limits||[]).includes(field.name)&&draft.limits[field.name]==null).map(field=>field.label.replace(/ \(.*$/,''));
  return <>
    <h3>Spending</h3>
    <p className="muted">{past.length>1?`Past full runs here with ${worker} cost ${money(Math.min(...past))} to ${money(Math.max(...past))}. A new run can differ.`:past.length?`The one past full run here with ${worker} cost ${money(past[0])}. A new run can differ.`:`No full run here with ${worker} yet, so there is no estimate.`} {project.free_providers.length?'':`This run stops at its budget of ${money(draft.budget_usd)}.`}</p>
    {open.length>0&&<p className="callout warn">No limit set: {open.join('; ')}. An agent can keep calling tools until the budget or a run-wide limit stops it. Set these under Advanced: run limits.</p>}
  </>;
}

function Review({project,jobs,limitFields,ui,close,done}){
  const {draft}=project,dialog=useRef(null),[check,setCheck]=useState(null),[checkError,setCheckError]=useState('');
  useEffect(()=>{dialog.current.showModal();},[]);
  useEffect(()=>{if(draft.task)api(`projects/${project.id}/check-test`,{}).then(setCheck,error=>setCheckError(error.message));},[draft.version]);
  const usesSpark=Object.values(draft.roles).includes('spark-contributor');
  const live=draft.mode==='live';
  const newHosts=live&&project.notice.some(entry=>!project.notice_accepted.includes(entry.host));
  const approve=()=>ui.act(async()=>{await api(`projects/${project.id}/approve`,{version:draft.version,data_approved:true,contributor_accepted:usesSpark,notice_accepted:newHosts});close();done();});
  return <dialog ref={dialog} className="review" onClose={close}>
    <h2>Approve version {draft.version}</h2>
    <dl>
      <dt>Mode</dt><dd>{live?'Live: real provider calls':'Preview: no live calls'}</dd>
      <dt>Samples</dt><dd>{project.inventory.inventory.length} files, frozen now</dd>
      <dt>Budget</dt><dd>${draft.budget_usd} for this run, ${project.budget.cap_usd} for the workspace</dd>
      {['orchestrator','improver','worker'].map(role=><React.Fragment key={role}><dt className="capital">{role}</dt><dd>{draft.providers[role].name}</dd></React.Fragment>)}
    </dl>
    {live&&<Spending project={project} jobs={jobs} limitFields={limitFields}/>}
    {draft.task&&<><h3>How the check scores</h3>{check?<CheckScores result={check}/>:checkError?<p className="callout bad" role="alert">{checkError}</p>:<p className="muted" role="status">Testing the check…</p>}</>}
    {project.external_orchestrator&&<p className="muted">A connected agent is the orchestrator. It uses its own model and account, so the costs shown here leave it out.</p>}
    {live&&<><h3>Who receives what</h3><ul className="plain">{project.notice.map(entry=><li key={entry.who}><strong>{entry.who}</strong> ({entry.host}): {entry.gets}</li>)}</ul></>}
    {usesSpark&&live&&<p className="callout warn">Spark Contributor may use approved content for training.</p>}
    {project.blocked&&<p className="callout bad" role="alert">{project.blocked}</p>}
    <div className="actions end"><button className="button" onClick={close}>Cancel</button><button className="button primary" disabled={ui.busy||Boolean(project.blocked)} onClick={approve}>{live?'Approve and accept':'Approve'}</button></div>
  </dialog>;
}
