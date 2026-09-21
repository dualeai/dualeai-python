"""Self-contained connect() tests over every endpoint shape.

Guards the shipped base_url bug: aiohttp rejects a path-bearing ``base_url``
without a trailing slash, so the Library session must bind to the endpoint
ORIGIN. connect() constructs both sessions without opening a socket (HPKE
discovery + encryption are lazy), so these run under ``--disable-socket``.
"""

import pytest

from dualeai.events.http_transport import HTTPTransport, _discovery_url, _endpoint_origin

_TOKEN = "dualeai_test_token_padded_1234"
_ORIGIN = "https://api.test.duale.ai"


@pytest.mark.unit
@pytest.mark.parametrize(
    "endpoint",
    [
        _ORIGIN,  # host-only
        f"{_ORIGIN}/",  # trailing slash
        f"{_ORIGIN}/http-bridge",  # path prefix, NO trailing slash — the shipped bug
        f"{_ORIGIN}/http-bridge/",  # path prefix + trailing slash
    ],
)
async def test_connect_accepts_all_endpoint_shapes(endpoint: str) -> None:
    """connect() succeeds for every endpoint shape and binds Library to the origin."""
    transport = HTTPTransport(endpoint=endpoint, token=_TOKEN)
    try:
        # RED before the fix: aiohttp raises "base_url must have a trailing '/'"
        # for the path-prefixed-no-slash endpoint.
        await transport.connect()
        assert transport.is_connected
        # Library base_url is the origin regardless of any bridge path prefix.
        library_session = transport._library_session
        assert library_session is not None
        assert str(library_session._base_url).rstrip("/") == _ORIGIN
    finally:
        await transport.disconnect()


@pytest.mark.unit
def test_endpoint_helpers_reject_schemeless_endpoint() -> None:
    """A scheme-less endpoint must fail loudly, not silently yield '://' or a relative URL."""
    for bad in ("api.duale.ai/http-bridge", "//api.duale.ai", "http-bridge"):
        with pytest.raises(ValueError, match="absolute http"):
            _endpoint_origin(bad)
        with pytest.raises(ValueError, match="absolute http"):
            _discovery_url(bad)


@pytest.mark.unit
def test_discovery_url_drops_query_and_fragment_keeps_path_prefix() -> None:
    """Discovery keeps the bridge path prefix but drops ?query/#fragment (RFC 3986)."""
    assert _discovery_url("https://api.duale.ai") == "https://api.duale.ai/.well-known/hpke-keys"
    assert (
        _discovery_url("https://api.duale.ai/http-bridge?x=1#f")
        == "https://api.duale.ai/http-bridge/.well-known/hpke-keys"
    )
    assert _endpoint_origin("https://api.duale.ai/http-bridge?x=1") == "https://api.duale.ai"
