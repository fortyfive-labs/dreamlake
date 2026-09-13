"""Ownership fault tests exercise remote fixture functions without sudo or SSH."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

SOURCE = Path(__file__).resolve().parents[1]/'scripts/fixtures/password/remote.py'


def fixture_functions():
    tree=ast.parse(SOURCE.read_text())
    definitions=ast.Module(body=[node for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef))],type_ignores=[])
    namespace={}
    exec(compile(definitions,str(SOURCE),'exec'),namespace)
    return namespace


@pytest.mark.parametrize('content',[None,'','{"pid":','{}'])
def test_missing_or_interrupted_state_recovers_only_exact_config_daemon(tmp_path,content):
    ns=fixture_functions();folder=tmp_path/'owned';folder.mkdir();proc=tmp_path/'proc';proc.mkdir()
    for pid in ('10','20','30'):(proc/pid).mkdir()
    if content is not None:(folder/'state.json').write_text(content)
    ns.update(folder=folder,Path=lambda *parts:proc if parts==('/proc',) else Path(*parts),proc=lambda pid:{'start':'99','exe':'/usr/sbin/sshd','cmd':['sshd','-f',str(folder/'sshd_config') if pid==20 else '/etc/ssh/sshd_config']})
    assert [pid for pid,_ in ns['daemon_candidates']()]==[20]


def test_atomic_state_failure_keeps_previous_receipt_and_recoverable_pending(tmp_path):
    ns=fixture_functions();ns['folder']=tmp_path
    original='{"pid": 10}'
    (tmp_path/'state.json').write_text(original)
    real=ns['os']
    def fail(*_):raise OSError('injected boundary')
    ns['os']=SimpleNamespace(fsync=real.fsync,replace=fail)
    with pytest.raises(OSError):ns['persist_state']({'pid':20})
    assert (tmp_path/'state.json').read_text()==original
    assert json.loads((tmp_path/'state.pending').read_text())=={'pid':20}


@pytest.mark.parametrize('case',['missing-home','foreign-marker','symlink-home','foreign-home','changed-uid'])
def test_partial_account_cleanup_requires_exact_marker_uid_and_home(case):
    ns=fixture_functions();tag='a'*12;user='dlpw-t-'+tag
    row=SimpleNamespace(pw_name=user,pw_uid=1001,pw_gid=1001,pw_gecos='dreamlake-password-'+tag+'-target',pw_dir='/home/'+user)
    if case=='foreign-marker':row.pw_gecos='unrelated'
    class Home:
        def lstat(self):
            if case in ('missing-home','changed-uid'):raise FileNotFoundError()
            return SimpleNamespace(st_mode=(stat.S_IFLNK if case=='symlink-home' else stat.S_IFDIR)|0o700,st_uid=9999 if case=='foreign-home' else 1001)
        def is_symlink(self):return case=='symlink-home'
    ns.update(tag=tag,users={'target':user},metadata={'accounts':{'target':{'uid':1002 if case=='changed-uid' else 1001}}},pwd=SimpleNamespace(getpwnam=lambda _:row),Path=lambda *_:Home(),os=SimpleNamespace(path=SimpleNamespace(lexists=lambda _:False)))
    if case=='missing-home':assert ns['account']('target',allow_missing_home=True) is row
    else:
        with pytest.raises(AssertionError):ns['account']('target',allow_missing_home=True)


@pytest.mark.parametrize('case',['term-kill','gone','reused','unsupported'])
def test_pidfd_signal_is_bound_to_verified_process_and_never_falls_back(tmp_path,case):
    ns=fixture_functions();signals=[];closed=[];current={'start':'42','exe':'/usr/sbin/sshd','cmd':['sshd','-f',str(tmp_path/'sshd_config')]}
    if case=='reused':current={**current,'start':'43'}
    class Poll:
        def __init__(self):self.calls=0
        def register(self,*_):pass
        def poll(self,_):
            self.calls+=1
            return [(8,1)] if self.calls>=3 else []
    os=SimpleNamespace(close=lambda fd:closed.append(fd))
    if case!='unsupported':os.pidfd_open=lambda pid,flags:8
    ns.update(folder=tmp_path,os=os,proc=lambda _:None if case=='gone' else current,
              select=SimpleNamespace(poll=Poll,POLLIN=1),signal=SimpleNamespace(SIGTERM=15,SIGKILL=9,pidfd_send_signal=lambda fd,sig:signals.append((fd,sig))))
    if case in ('reused','unsupported'):
        with pytest.raises(AssertionError):ns['stop_owned'](123,{'start':'42'})
    else:ns['stop_owned'](123,{'start':'42'})
    assert signals==([(8,15),(8,9)] if case=='term-kill' else [])
    assert closed==([] if case=='unsupported' else [8])
