"""Request serialization when the caller supplies Content-Type as a header."""

from email import policy
from email.parser import BytesParser
from xml.etree import ElementTree

import httpx
import pytest

from api_parity.executor import Executor
from api_parity.models import RequestCase, TargetConfig


@pytest.mark.parametrize(("content_type", "body", "expected"), [
    ("application/json", {"name": "hello world"}, b'{"name":"hello world"}'),
    ("Application/Vnd.Api+Json; charset=utf-8", [True, False], b'[true,false]'),
    ("application/xml", {"root": {"name": "hello world"}}, None),
    ("application/json", '{ "name": "hello" }', b'{ "name": "hello" }'),
    ("application/json", b'{ "name": "hello" }', b'{ "name": "hello" }'),
    ("application/x-www-form-urlencoded", {"name": "hello world"}, b"name=hello+world"),
    ("Application/X-WWW-Form-Urlencoded; charset=utf-8",
     {"name": "a+b", "tag": ["x", "y"]}, b"name=a%2Bb&tag=x&tag=y"),
    ("application/x-www-form-urlencoded", "name=hello%20world", b"name=hello%20world"),
    ("application/x-www-form-urlencoded", b"name=%ff", b"name=%ff"),
    ("multipart/form-data; boundary=custom", {"name": "hello world"}, None),
    ("multipart/form-data; boundary=custom", b"--custom--\r\n", b"--custom--\r\n"),
])
def test_content_type_header(monkeypatch, content_type, body, expected):
    requests = []

    def capture(request):
        requests.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(Executor, "_build_client_kwargs", lambda self, config, timeout: {
        "base_url": config.base_url, "transport": httpx.MockTransport(capture),
    })
    case = RequestCase(case_id="form", operation_id="submit", method="POST", path_template="/",
                       rendered_path="/", headers={"content-TYPE": [content_type]}, body=body)
    with Executor(TargetConfig(base_url="http://a"), TargetConfig(base_url="http://b")) as executor:
        executor.execute(case)

    assert [request.url.host for request in requests] == ["a", "b"]
    for request in requests:
        assert request.headers["content-type"] == content_type
        if expected is not None:
            assert request.content == expected
        elif content_type == "application/xml":
            root = ElementTree.fromstring(request.content)
            assert (root.tag, root.findtext("name")) == ("root", "hello world")
        else:
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\n\r\n".encode() + request.content)
            assert [(part.get_param("name", header="content-disposition"), part.get_payload(decode=True))
                    for part in message.iter_parts()] == [("name", b"hello world")]
