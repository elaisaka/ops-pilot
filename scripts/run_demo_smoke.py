"""Start real local uvicorn, demonstrate authenticated workflow, never invoke a model."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.auth import create_demo_credentials


def main():
    with tempfile.TemporaryDirectory(prefix="ops-demo-start-") as directory:
        path, tokens = create_demo_credentials(Path(directory))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'AGENT_MODE':'langgraph','KNOWLEDGE_RETRIEVER':'candidate',
               'DEEPSEEK_API_KEY':'','DATABASE_PATH':str(Path(directory)/'demo.db'),
               'DEMO_AUTH_FILE':str(path),'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}
        process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1',
                                    '--port',str(port),'--no-access-log'],cwd=ROOT,env=env,
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Local server exited before readiness')
                    try:
                        if client.get('/health').status_code==200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('Local server readiness timed out')
                assert client.get('/api/orders').status_code==401
                cid=client.post('/api/conversations').json()['conversation_id']
                url=f'/api/conversations/{cid}/messages'
                knowledge=client.post(url,json={'message':'平台交易信息应保存多久'}).json()
                assert knowledge['citations'] and knowledge['retrieval']['retriever']=='candidate'
                client.headers['Authorization']='Bearer '+tokens['operator']
                assert len(client.get('/api/orders').json())>0
                for kind, query in [('delivery','订单 ORD-10002 物流异常'),('invoice','订单 ORD-10001 申请电子发票')]:
                    reply=client.post(url,json={'message':query},headers={'Idempotency-Key':kind}).json()
                    ticket=reply['tickets'][0]
                    assert ticket['category']==kind
                    assert client.patch('/api/tickets/'+ticket['id'],json={'status':'in_progress'}).status_code==200
                reply=client.post(url,json={'message':'订单 ORD-10001 退款'},headers={'Idempotency-Key':'refund'}).json()
                approval=reply['approvals'][0]
                assert approval['status']=='pending'
                decision='/api/approvals/'+approval['id']+'/decision'
                assert client.post(decision,json={'approved':True}).status_code==403
                client.headers['Authorization']='Bearer '+tokens['supervisor']
                assert client.post(decision,json={'approved':True}).status_code==200
                assert client.post(decision,json={'approved':True}).status_code==409
                replay=client.post(url,json={'message':'订单 ORD-10001 退款'},headers={'Idempotency-Key':'refund'}).json()
                assert replay['approvals'][0]['id']==approval['id']
                client.headers['Authorization']='Bearer '+tokens['auditor']
                audit=client.get(f'/api/conversations/{cid}/audit').json()
                assert sum(r['event_type']=='approval_requested' for r in audit)==1
                assert sum(r['event_type']=='approval_decided' for r in audit)==1
                assert client.post(decision,json={'approved':True}).status_code==403
                print('PASS: real local uvicorn + anonymous Q&A/citations + simulated orders + delivery/invoice + pending/decision + RBAC + replay + audit; no model API')
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__=='__main__':
    main()
