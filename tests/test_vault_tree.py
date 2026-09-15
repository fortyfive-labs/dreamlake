import copy
import pytest
from dreamlake.vault_tree import validate_tree, tree
from dreamlake.vault import VaultError


def sample():
    return dict(schemaVersion=1, prefix='alice', observedAt='2026-09-15T00:00:00.000Z', rows=[dict(kind='prefix',path='alice',parentPath=None,policy=dict(state='known',reason=None,governingPrefix=None,keyRef='managed',keyId='arn:aws:kms:us-east-1:123456789012:key/test',provider='aws-kms',epoch=None,migration=dict(state='none',requestId=None)))], nextCursor=None)


def test_exact_schema_and_plaintext_rejection():
    validate_tree(sample(), 'alice', 100)
    for field in ('value', 'encrypted', 'secret'):
        value=sample();value['rows'][0][field]='SYNTHETIC'
        with pytest.raises(VaultError, match='Invalid vault tree metadata'):
            validate_tree(value,'alice',100)
    value=sample();del value['rows'][0]['parentPath']
    with pytest.raises(VaultError):validate_tree(value,'alice',100)
    value=sample();value['rows'].append(copy.deepcopy(value['rows'][0]))
    with pytest.raises(VaultError):validate_tree(value,'alice',100)


def test_tree_is_one_metadata_request_and_validates_before_request():
    calls=[]
    class Fake:
        def _request(self,*args,**kwargs):
            calls.append((args,kwargs));return sample()
    result=tree(Fake(),prefix='alice',limit=2,cursor='opaque')
    assert result['nextCursor'] is None
    assert calls==[(('GET','/v1/vault/tree'),{'params':{'prefix':'alice','limit':'2','cursor':'opaque'}})]
    for kwargs in ({'prefix':''},{'prefix':'alice','limit':True},{'prefix':'alice','cursor':'a/b'}):
        with pytest.raises(VaultError):tree(Fake(),**kwargs)
    assert len(calls)==1
