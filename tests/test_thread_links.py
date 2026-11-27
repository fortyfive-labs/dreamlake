import pytest

from dreamlake.threads import thread_url, threads_url


def test_thread_links():
    thread_id = "1ad5a956-c5fa-47c0-a9dc-9d29d2612c42"
    assert threads_url("example-org") == "https://dreamlake.ai/example-org/sessions"
    assert thread_url(thread_id.upper(), session="archive:source:conversation") == (
        f"https://dreamlake.ai/threads/{thread_id}?session=archive%3Asource%3Aconversation"
    )
    with pytest.raises(ValueError):
        thread_url(thread_id, session="../not-a-session")
    with pytest.raises(ValueError):
        threads_url("../not-a-namespace")
