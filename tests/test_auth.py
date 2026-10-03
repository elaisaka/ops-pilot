import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.mark.parametrize("role", [None, "operator", "supervisor", "auditor"])
def test_role_matrix_and_no_self_reported_escalation(tmp_path, demo_credentials, role):
    auth = "Bearer " + demo_credentials[role] if role else ""
    with TestClient(create_app(tmp_path / "roles.db"), headers={"Authorization": auth, "X-Role": "supervisor"}) as client:
        cid = client.post('/api/conversations').json()['conversation_id']
        url = f'/api/conversations/{cid}/messages'
        first = client.post(url, json={'message':'订单 ORD-10001 退款'}, headers={'Idempotency-Key':'one'})
        repeated = client.post(url, json={'message':'订单 ORD-10001 退款'}, headers={'Idempotency-Key':'one'})
        assert first.status_code == repeated.status_code == 200
        approval = first.json()['approvals'][0]['id']
        assert repeated.json()['approvals'][0]['id'] == approval
        decision_url = f'/api/approvals/{approval}/decision'
        decision = client.post(decision_url,json={'approved':True,'decided_by':'supervisor'})
        assert decision.status_code == (200 if role=='supervisor' else 403 if role else 401)
        store = client.app.state.service.store
        if role=='supervisor':
            assert decision.json()['approval']['decided_by']=='demo-supervisor'
            assert client.post(decision_url,json={'approved':True}).status_code==409
            assert len(store.list_tickets())==1
        else:
            assert len(store.list_pending_approvals())==1 and store.list_tickets()==[]
        delivery=client.post(url,json={'message':'订单 ORD-10002 物流异常'},headers={'Idempotency-Key':'two'}).json()
        ticket=delivery['tickets'][0]['id']
        patch=client.patch(f'/api/tickets/{ticket}',json={'status':'in_progress','assignee':'spoof'})
        assert patch.status_code==(200 if role in ('operator','supervisor') else 403 if role else 401)
        assert client.get(f'/api/conversations/{cid}/audit').status_code==(200 if role in ('supervisor','auditor') else 403 if role else 401)
        for path in ('orders','tickets','approvals','dashboard'):
            assert client.get('/api/'+path).status_code==(200 if role else 401)
        rows=store.list_audit(cid)
        assert sum(row['event_type']=='approval_requested' for row in rows)==1
        assert sum(row['event_type']=='approval_decided' for row in rows)==(1 if role=='supervisor' else 0)
        assert any(row['event_type']=='business_action_reused' for row in rows)
        assert not any(token in str(rows) for token in demo_credentials.values())


def test_invalid_token_and_missing_configuration_fail_closed(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'auth.db'),headers={'Authorization':'Bearer '+('invalid-'*8)}) as client:
        assert client.get('/api/auth/me').status_code==401
        monkeypatch.setenv('DEMO_AUTH_FILE',str(tmp_path/'missing.json'))
        reply=client.get('/api/auth/me')
        assert reply.status_code==503 and str(tmp_path) not in reply.text
        assert client.get('/health').status_code==200
