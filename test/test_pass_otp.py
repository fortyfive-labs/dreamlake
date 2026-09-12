import json
from pathlib import Path
import pytest
from dreamlake.pass_otp import parse_otp_uri, preview_pass_otp

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/pass-otp.json').read_text())

@pytest.mark.parametrize('fixture', FIXTURES)
def test_shared_uri_fixtures(fixture):
    if fixture.get('invalid'):
        with pytest.raises(ValueError, match='^Invalid or unsupported OTP registration$'):
            parse_otp_uri(fixture['uri'])
    else:
        parsed = parse_otp_uri(fixture['uri']).reveal()
        for key, value in fixture.items():
            if key != 'uri':
                assert parsed[key] == value


def test_preview_redaction():
    uri = FIXTURES[0]['uri']
    assert 'JBSWY3DPEHPK3PXP' not in repr(parse_otp_uri(uri))
    preview = preview_pass_otp([dict(path='registered', content='ADJACENT_PASSWORD\n' + uri), dict(path='plain', content='ADJACENT_PASSWORD'), dict(path='ambiguous', content=uri+'\n'+uri)])
    assert (preview['ready'], preview['skipped'], preview['invalid']) == (1, 1, 1)
    assert 'ADJACENT_PASSWORD' not in json.dumps(preview)
    assert 'JBSWY3DPEHPK3PXP' not in json.dumps(preview)
