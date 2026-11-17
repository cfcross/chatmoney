"""Upload boundaries, review scope and cleanup. Never uses live files or accounts."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from starlette.requests import Request
from starlette import formparsers
from app import knowledge, knowledge_upload as up, schema as s
from app.db import Problem, transaction


def make_book(env):
    return env.req("POST","/knowledge-bases",body={"name":"容量验收","product_id":"pA1"}).json()["data"]["kb_id"]


def upload(env,kb,raw,name="source.md",actor="tob-admin",key=None,**fields):
    return env.req("POST",f"/knowledge-bases/{kb}/documents",actor,key=key,
                   files={"file":(name,raw,"text/plain")},data={"title":"验收资料",**fields})


def preview(env,doc,actor="tob-admin",offset=0):
    return env.client.get("/console/data/documents/"+doc,params={"offset":offset},
                          headers={"Authorization":"Bearer "+env.tokens[actor]})


def test_large_source_roundtrip_review_and_index_horizon(env):
    kb = make_book(env)
    raw = b"\xef\xbb\xbfstartmarker" + ("企业型号说明"*120000).encode() + b"tailmarker"
    assert len(raw)>up.INDEX_LIMIT
    r = upload(env,kb,raw)
    assert r.status_code==202,r.text
    doc = r.json()["data"]["document_id"]
    assert preview(env,doc,"tob-business").status_code==404
    with transaction("tob") as c:
        assert hashlib.sha256(b"".join(up.blocks(c,"tob",doc))).digest()==hashlib.sha256(raw).digest()
        knowledge.index(c,"tob",doc)
    ref = {"document_id":doc,"revision":1}
    cfg = {"knowledge":[{"kb_id":kb,"snapshot":1}]}
    assert env.req("POST",f"/knowledge-bases/{kb}/publish",body={"expected_snapshot":None,"documents":[ref]}).status_code==201
    first = preview(env,doc,"tob-business").json()["data"]
    second = preview(env,doc,"tob-business",first["next_offset"]).json()["data"]
    assert (first["text"]+second["text"]).encode()==raw[3:second["next_offset"]]
    assert preview(env,doc,"toc-admin").status_code==404
    assert preview(env,doc,offset=len(raw)-10).json()["data"]["text"]=="tailmarker"
    with transaction("tob") as c:
        assert knowledge.retrieve(c,"tob","pA1",cfg,"startmarker")
        assert not knowledge.retrieve(c,"tob","pA1",cfg,"tailmarker")
    assert env.req("POST",f"/knowledge-bases/{kb}/withdraw",body=ref|{"reason":"测试"}).status_code==200
    assert preview(env,doc,"tob-business").status_code==404


@pytest.mark.parametrize("name,body",[("notes.pdf",b"xx"),("../source.md",b"hello"),("a.md",b"x"*(up.INLINE+1)+b"\xff"),("a.txt",b"\xef\xbb\xbf \r\n"),("a.txt",b"x\x00y")])
def test_rejected_source_creates_no_document(env,name,body):
    kb = make_book(env)
    assert upload(env,kb,body,name).status_code==415
    assert env.req("GET","/knowledge-bases/"+kb).json()["data"]["documents"]==[]


def test_quota_slot_and_disk_fail_before_mutation(env,monkeypatch):
    kb=make_book(env)
    with up.upload_slot("tob"):
        assert upload(env,kb,b"hello").status_code==429
    monkeypatch.setattr(up.settings(),"upload_actor_bytes",2)
    assert upload(env,kb,b"hello").json()["error"]["code"]=="STORAGE_QUOTA"
    monkeypatch.setattr(up.shutil,"disk_usage",lambda _: SimpleNamespace(free=1))
    assert upload(env,kb,b"hello").status_code==507
    assert env.req("GET","/knowledge-bases/"+kb).json()["data"]["documents"]==[]


def test_duplicates_replacements_and_cleanup(env):
    kb=make_book(env)
    raw=b"approved information\n"*65000
    assert upload(env,kb,raw,actor="tob-business").status_code==403
    a=upload(env,kb,raw,key="unique-source")
    assert a.status_code==202,a.text
    doc=a.json()["data"]["document_id"]
    assert upload(env,kb,raw,key="unique-source").json()["data"]==a.json()["data"]
    assert upload(env,kb,raw+b"x",key="unique-source").status_code==409
    replacement=upload(env,kb,raw+b"z",replace_id=doc,expected_revision="1")
    assert replacement.status_code==202,replacement.text
    new=replacement.json()["data"]["document_id"]
    assert upload(env,kb,raw,replace_id=doc,expected_revision="1").status_code==409
    r=env.client.delete(f"/console/manage/knowledge-bases/{kb}/documents/{new}",headers={
       "Authorization":"Bearer "+env.tokens["tob-admin"],"Idempotency-Key":"delete-source"})
    assert r.status_code==200,r.text
    with transaction("tob") as c:
        assert c.scalar(select(func.count()).select_from(s.source_blocks).where(s.source_blocks.c.document_id.in_([doc,new])))==0


def test_other_agent_can_run_while_upload_slot_is_occupied(env):
    from tests.test_workflows import prepare, plan
    from app import workflows
    _, sid = prepare(env)
    with up.upload_slot("tob"):
        result = workflows.drive(plan(env, "tob", sid, "请给正式报价"), sid)
    assert result["action"] == "handoff" and result["delivery"] == "not_sent"


@pytest.mark.parametrize("mode,code",[("disconnect","UPLOAD_INTERRUPTED"),("truncated","INVALID_UPLOAD"),("understated","FILE_TOO_LARGE")])
def test_stream_counts_real_bytes_and_closes_spool(env,monkeypatch,mode,code):
    real=formparsers.SpooledTemporaryFile
    files=[]
    def opened(*a,**kw):
        f=real(*a,**kw)
        files.append(f)
        return f
    monkeypatch.setattr(formparsers,"SpooledTemporaryFile",opened)
    monkeypatch.setattr(up,"FILE_LIMIT",up.INLINE+2)
    prefix=b'--cm\r\nContent-Disposition: form-data; name="file"; filename="x.md"\r\n\r\n'
    parts=[prefix, b"x"*(up.INLINE+1)]
    if mode=="understated":
        parts.extend([b"x"*100,b"\r\n--cm--\r\n"])
    async def receive():
        if parts:
            return {"type":"http.request","more_body":True,"body":parts.pop(0)}
        return {"type":"http.disconnect"} if mode=="disconnect" else {"type":"http.request","more_body":False,"body":b""}
    request=Request({"type":"http","method":"POST","path":"/","headers":[(b"content-type",b"multipart/form-data; boundary=cm"),(b"content-length",b"5")]},receive)
    with pytest.raises(Problem) as raised:
        asyncio.run(up.receive_form(request))
    assert raised.value.code==code
    assert files and all(f.closed for f in files)
