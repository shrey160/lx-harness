import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {api, setToken} from './api.js';
import {NewWorkspace} from './NewWorkspace.jsx';
import {Settings} from './Settings.jsx';
import {Sidebar} from './Sidebar.jsx';
import {Workspace} from './Workspace.jsx';

function App(){
  const [state,setState]=useState(null),[view,setView]=useState({page:'new'});
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const refresh=async()=>{const next=await api('state');setState(next);return next;};
  useEffect(()=>{
    let open=true;
    api('session').then(s=>{setToken(s.token);return refresh();}).catch(e=>setError(e.message));
    const timer=setInterval(()=>{if(open)refresh().catch(()=>{});},1200);
    return()=>{open=false;clearInterval(timer);};
  },[]);
  useEffect(()=>{
    if(!notice)return undefined;
    const timer=setTimeout(()=>setNotice(''),5000);
    return()=>clearTimeout(timer);
  },[notice]);
  const act=async fn=>{
    setError('');setNotice('');setBusy(true);
    try{const result=await fn();await refresh();return result;}
    catch(e){setError(e.message);}
    finally{setBusy(false);}
  };
  const ui={busy,act,notify:setNotice,go:setView};
  const project=view.page==='workspace'&&state?.projects.find(p=>p.id===view.id);
  return <div className="shell">
    <Sidebar state={state} view={view} go={setView}/>
    <main className="main">
      {!state&&!error&&<p className="empty">Opening…</p>}
      {state&&view.page==='new'&&<NewWorkspace ui={ui}/>}
      {state&&view.page==='settings'&&<Settings state={state} ui={ui} section={view.section||'providers'}/>}
      {project&&<Workspace key={project.id} state={state} project={project} ui={ui} tab={view.tab||'spec'}/>}
    </main>
    {(error||notice)&&<div className="toasts">
      {error&&<div className="toast bad" role="alert"><span>{error}</span><button className="link" onClick={()=>setError('')}>Dismiss</button></div>}
      {notice&&<div className="toast" role="status"><span>{notice}</span><button className="link" onClick={()=>setNotice('')}>Dismiss</button></div>}
    </div>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
