"""Record and reopen local experiment data through the public DreamLake API."""
import numpy as np
from dreamlake import Experiment, OperationMode, experiment


def test_local_recording_formats_and_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAMLAKE_EXPERIMENT_BUFFER_ENABLED', 'false')
    prefix = 'lab/research/sweep/实验 α'
    with Experiment(prefix=prefix, root=tmp_path) as run:
        assert run.mode == OperationMode.LOCAL
        run.params.set(model={'depth': 4}, exact=.1+.2)
        run.logs.info('recorded locally')
        run.metrics('train').log(step=0, loss=.25)
        run.tracks('robot/pose').append(x=1., _ts=1.)
        run.files.save_json({'x': [1, 2]}, to='config.json')
        run.files.save_text('中文\n', to='参数,2".txt')
        run.files.save_blob(b'\x00\xff\r\n', to='weights.bin')
        run.files().save_pkl({'array': np.array([1.,2.])}, to='model.pkl')
        run.files().save_image(np.zeros((2, 2, 3), dtype=np.uint8), to='image.png')
        run.flush()
        assert run.params.get()['exact'] == .1+.2
        assert run.tracks('robot/pose').read()['entries'][0]['x'] == 1.
        assert run.files('参数,2".txt').read_text() == '中文\n'
        assert len(run.files.list()) == 5
    with Experiment(prefix=prefix, root=tmp_path) as run:
        assert run.params.get()['model.depth'] == 4
        assert run.metrics('train').read()['data'][0]['data']['loss'] == .25
        target = tmp_path/'returned.bin'
        run.files('weights.bin').download(to=str(target))
        assert target.read_bytes() == b'\x00\xff\r\n'


def test_recording_decorator(tmp_path):
    @experiment(prefix='lab/research/decorated', root=tmp_path)
    def record(experiment):
        experiment.params.set(seed=7)
        return 7
    assert record() == 7
