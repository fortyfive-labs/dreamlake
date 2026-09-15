import httpx
import pytest
from dreamlake import DreamLakeClient
from dreamlake.api.runs import RunError
CAPS = {'executionKinds': ['uv-run', 'uvx'],
 'privateSetup': {'enabled': True,
                  'hostReadiness': 'verified-on-submit',
                  'maxArgBytes': 8192,
                  'maxArgv': 128,
                  'maxMappings': 100,
                  'maxTimeoutSeconds': 86400,
                  'outputPolicy': 'discard-at-source-v1',
                  'repositoryOrigins': ['https://github.com'],
                  'requiresConsent': True,
                  'requiresExplicitEnrollment': True},
 'version': 1}
def test_capability_projection_and_invalid_responses():
    values=[{**CAPS,"debug":"DO_NOT_ECHO","privateSetup":{**CAPS["privateSetup"],"token":"DO_NOT_ECHO"}}]
    def handler(req):
        assert req.url.path=="/namespaces/alice/runs/capabilities"
        return httpx.Response(200,json=values[0])
    runs=DreamLakeClient(token="synthetic",transport=httpx.MockTransport(handler)).runs
    assert runs.capabilities("alice")==CAPS
    for patch in [{"enabled":"true"},{"repositoryOrigins":["https://secret@github.com"]},{"repositoryOrigins":["http://github.com"]},{"maxArgv":True},{"hostReadiness":"online"}]:
        values[0]={**CAPS,"privateSetup":{**CAPS["privateSetup"],**patch}}
        with pytest.raises(RunError) as e:runs.capabilities("alice")
        assert "secret" not in str(e.value)
