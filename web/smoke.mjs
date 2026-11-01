// Renders every page once from a state file, so a page that throws on open fails here and not in front of a user.
// Effects do not run, so pages that load their data in an effect show only their first paint.
// node smoke.mjs <state.json>
import {build} from 'esbuild';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const entry=`
import React from 'react';
import {renderToString} from 'react-dom/server';
import {NewWorkspace} from './src/NewWorkspace.jsx';
import {RunPage} from './src/RunsTab.jsx';
import {Settings} from './src/Settings.jsx';
import {Sidebar} from './src/Sidebar.jsx';
import {Workspace} from './src/Workspace.jsx';

export function pages(state){
  const ui={busy:false,act:()=>{},notify:()=>{},go:()=>{}},found=[];
  const page=(name,element)=>found.push([name,()=>renderToString(element)]);
  page('sidebar',<Sidebar state={state} view={{page:'new'}} go={()=>{}}/>);
  page('new workspace',<NewWorkspace ui={ui}/>);
  for(const section of ['providers','tools','mcp','connectivity','usage','general'])page('settings '+section,<Settings state={state} ui={ui} section={section}/>);
  for(const project of state.projects){
    for(const tab of ['samples','discuss','spec','runs'])page(project.name+' '+tab,<Workspace state={state} project={project} ui={ui} tab={tab}/>);
    for(const job of state.jobs.filter(item=>item.project_id===project.id))page(project.name+' run '+job.id,<RunPage project={project} job={job} ui={ui} back={()=>{}} canTry tryHarness={()=>{}}/>);
  }
  return found;
}
`;
const folder=await mkdtemp(join(tmpdir(),'lx-smoke-'));
try{
  const bundle=join(folder,'pages.mjs');
  await build({stdin:{contents:entry,resolveDir:import.meta.dirname,loader:'jsx'},bundle:true,format:'esm',platform:'node',jsx:'automatic',outfile:bundle,
    define:{'process.env.NODE_ENV':'"production"'},banner:{js:"import {createRequire} from 'node:module';const require=createRequire(import.meta.url);"},logLevel:'error'});
  const {pages}=await import(pathToFileURL(bundle).href);
  const state=JSON.parse(await readFile(process.argv[2],'utf-8')),failed=[],found=pages(state);
  for(const [name,render] of found){
    try{render();}catch(error){failed.push(`${name}: ${error.message}`);}
  }
  console.log(failed.length?failed.join('\n'):`${found.length} pages rendered`);
  process.exitCode=failed.length?1:0;
}finally{
  await rm(folder,{recursive:true,force:true});
}
