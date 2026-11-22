"""Persisted request templates and execution traces must reproduce the request bytes."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from api_parity.artifact_writer import ArtifactWriter
from api_parity.bundle_loader import load_bundle
from api_parity.executor import Executor
from api_parity.models import (
    ChainCase, ChainStep, ComparisonResult, MismatchType, RequestCase, ResponseCase,
    SecretsConfig, TargetConfig, TargetInfo,
)


@pytest.mark.parametrize("stateful", [False, True], ids=["stateless", "chain"])
def test_request_body_round_trip(tmp_path, monkeypatch, stateful):
    binary = bytes(range(256))
    formatted = b'{ "name" : "{id}" }'
    cases = [
        ("application/octet-stream", binary, None, binary),
        ("application/octet-stream", b"", None, b""),
        ("application/json", formatted, None, b'{"name":"{id}"}'),
        ("application/json", None, base64.b64encode(formatted).decode(), formatted),
        ("application/problem+json", binary, None, binary),
        ("application/json", b"null", None, b""),
        ("text/plain", "b'abc'", None, b"b'abc'"),
    ]
    writer = ArtifactWriter(tmp_path, spec_path=Path(__file__).parent / "fixtures/test_api.yaml")
    target = TargetInfo(name="local", base_url="http://example.test")
    diff = ComparisonResult(match=False, mismatch_type=MismatchType.STATUS_CODE, summary="200/404", details={})
    wire = []

    def record(request):
        wire.append((request.headers["content-type"], request.content))
        return httpx.Response(200 if request.url.host == "a" else 404)

    monkeypatch.setattr(Executor, "_build_client_kwargs", lambda self, config, timeout: {
        "base_url": config.base_url, "transport": httpx.MockTransport(record),
    })
    with Executor(TargetConfig(base_url="http://a"), TargetConfig(base_url="http://b")) as executor:
        for index, (media, body, encoded, expected) in enumerate(cases):
            case = RequestCase(case_id=str(index), operation_id="upload", method="POST",
                               path_template="/upload", rendered_path="/upload",
                               body=body, body_base64=encoded, media_type=media)
            wire.clear()
            if stateful:
                chain = ChainCase(chain_id=str(index), steps=[ChainStep(step_index=0, request_template=case)])
                trace_a, trace_b = executor.execute_chain(chain)
                path = writer.write_chain_mismatch(chain, trace_a, trace_b, [diff], 0, target, target)
                executor.execute_chain(load_bundle(path).chain_case)
            else:
                response_a, response_b = executor.execute(case)
                path = writer.write_mismatch(case, response_a, response_b, diff, target, target)
                executor.execute(load_bundle(path).request_case)
            assert wire == [(media, expected)] * 4
            for filename in ("target_a.json", "target_b.json"):
                trace = json.loads((path / filename).read_text())
                request = (trace["steps"][0] if stateful else trace)["request"]
                if isinstance(body, bytes):
                    assert request["body"] is None
                    assert base64.b64decode(request["body_base64"], validate=True) == body
            assert case.body == body


def test_whole_body_redaction_applies_before_binary_encoding(tmp_path):
    case = RequestCase(case_id="secret", operation_id="upload", method="POST", path_template="/",
                       rendered_path="/", body=b"sensitive", media_type="application/octet-stream")
    writer = ArtifactWriter(tmp_path, SecretsConfig(redact_fields=["$..body"]))
    target = TargetInfo(name="t", base_url="http://t")
    response = ResponseCase(status_code=200, elapsed_ms=1)
    diff = ComparisonResult(match=False, mismatch_type=MismatchType.STATUS_CODE, summary="test", details={})
    bundle = writer.write_mismatch(case, response, response, diff, target, target)
    for filename in ("case.json", "target_a.json", "target_b.json"):
        record = json.loads((bundle / filename).read_text())
        request = record.get("request", record)
        assert request["body"] == "[REDACTED]"
        assert request["body_base64"] is None
