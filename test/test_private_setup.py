import json
from pathlib import Path
import httpx
import pytest
from dreamlake import DreamLakeClient
from dreamlake.api._run_setup import validate_setup
from dreamlake.api.runs import RunConfigurationError
VECTORS = json.loads((Path(__file__).parent / "fixtures/private-setup.json").read_text())
@pytest.mark.parametrize("v", VECTORS, ids=lambda v:v["name"])
def test_vectors(v):
    if v["valid"]:
        assert validate_setup(v["setup"]) == v["setup"]
    else:
        with pytest.raises(RunConfigurationError) as e: validate_setup(v["setup"])
        assert "DO_NOT_ECHO" not in str(e.value)

def test_submit_private_no_source_reads(tmp_path):
    seen=[]
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(202,json={"run":{"id":"one","status":"queued"}})
    runs=DreamLakeClient(token="synthetic",transport=httpx.MockTransport(handler)).runs
    args=dict(kind="uv-run",argv=["main.py","--setup","opaque"],enrollment_id="a"*24,setup=VECTORS[0]["setup"],allow_vault_delivery=True,request_id="retry")
    assert runs.submit("alice/lab/host",cwd=tmp_path/"nonexistent",**args)["id"]=="one"
    assert "source" not in seen[0] and seen[0]["allowVaultDelivery"] is True
    assert seen[0]["setup"]==args["setup"]
    for change in [{"allow_vault_delivery":False},{"include":["not-read"]},{"enrollment_id":None},{"kind":"uvx"},{"argv":["界"*2731]},{"request_id":"bad id"},{"allow_vault_delivery":1}]:
        with pytest.raises(RunConfigurationError): runs.submit("alice/lab/host",**{**args,**change})
    assert len(seen)==1
