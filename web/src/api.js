let token='';
export function setToken(value){token=value;}
export async function api(path,body){
  const response=await fetch('/api/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-LX-Token':token},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Request failed');
  return result;
}
export const money=n=>{
  if(n==null)return 'Unknown';
  const value=Number(n);
  if(value===0)return '$0.00';
  if(value<0.0001)return '<$0.0001';
  return `$${value.toFixed(value<1?4:2)}`;
};
// A task reports base and after-the-loop means; the demo's are per candidate, so it has none to show.
export const scores=result=>result?.means&&['base','harness2'].some(key=>typeof result.means[key]==='number')?result.means:null;
// A score the judge could not give is left out, never shown as zero.
export const two=value=>typeof value==='number'?value.toFixed(2):'not judged';
export const plural=(n,word='call')=>`${n} ${word}${n===1?'':'s'}`;
export const when=seconds=>new Date(seconds*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
export const active=job=>['queued','running'].includes(job?.status);
export const resumable=job=>['failed','cancelled','interrupted'].includes(job?.status);
export const modeName=mode=>mode==='live'?'Live':'Preview';
export const runName=run=>run.kind==='trial'?(run.from_run?'Trial of a package':'Trial'):run.mode==='live'?'Full run':'Preview';
const STATUS={queued:'Waiting to start',running:'Running',completed:'Completed',failed:'Failed',cancelled:'Stopped',interrupted:'Interrupted',timed_out:'Timed out'};
const PHASE={preflight:'Checking providers',improving:'Improving the harness',trying:'Trying one sample',grading:'Grading'};
export const statusName=job=>job.status==='running'&&PHASE[job.phase]||STATUS[job.status]||job.status;
export const tone=job=>active(job)?'run':job?.status==='completed'?'ok':job?'warn':'idle';
