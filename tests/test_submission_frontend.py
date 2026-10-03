"""Exercise actual page JavaScript without a network or browser service."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_page_retries_same_submission_after_failure_and_reload():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const script = fs.readFileSync('app/static/index.html','utf8').split('<script>')[1].split('</script>')[0];
const saved = new Map(), calls = []; let serial=0, fail=true;
const storage = {getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
function page() {
  const nodes = new Map();
  const element = () => ({value:'',append(){},scrollIntoView(){},addEventListener(){}});
  const context = {sessionStorage:storage, crypto:{randomUUID:()=>`key-${++serial}`}, window:{},
    document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},createElement:element},
    fetch:async(url, options)=>{
      if(url.endsWith('/messages')) { calls.push(options); if(fail) throw Error('offline');
        return {ok:true,json:async()=>({message:'saved',citations:[],retrieval:null})}; }
      if(url==='/api/conversations')return {ok:true,json:async()=>({conversation_id:'conversation-1'})};
      throw Error('panel unavailable');
    }};
  vm.createContext(context);vm.runInContext(script,context);return {context,nodes};
}
const tick = () => new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let p=page();await tick();p.nodes.get('input').value='物流查询';
  const event={preventDefault(){}};
  const running=p.context.submitMessage(event);
  await p.context.submitMessage(event);await running;
  assert.equal(calls.length,1); assert.equal(saved.size,1);
  const first=calls[0];
  p=page();await tick(); fail=false;
  await p.context.submitMessage(event);await tick();
  assert.equal(calls[1].headers['Idempotency-Key'],first.headers['Idempotency-Key']);
  assert.equal(calls[1].body,first.body);assert.equal(saved.size,0);
  assert.equal(p.nodes.get('send').textContent,'发送');
  p.nodes.get('input').value='物流查询';await p.context.submitMessage(event);
  assert.notEqual(calls[2].headers['Idempotency-Key'],first.headers['Idempotency-Key']);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                   check=True, capture_output=True, text=True)
