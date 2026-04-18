"""Bootstrap monkey-patch for charli3_dendrite.

Upstream bug: `charli3_dendrite.backend.blockfrost.BlockFrostBackend.__init__`
hardcodes `base_url=ApiUrls.mainnet.value`, so any preprod/preview project_id
hits 'Network token mismatch' (403) when the backend is constructed.

Two consequences this patch fixes:
  1. `set_default_backend()` at the bottom of `charli3_dendrite/backend/__init__.py`
     runs at module-import time. With BLOCKFROST_PROJECT_ID in env, it tries
     to construct BlockFrostBackend -> 403. We hide the env var across that
     import, then restore.
  2. `setup_dendrite_backend()` later wants to call
     `set_backend(BlockFrostBackend(project_id))` for testnet -- same crash
     until BlockFrostBackend is patched to pick a base_url from the
     project_id prefix.
"""
import os

_saved_pid = os.environ.pop("BLOCKFROST_PROJECT_ID", None)

from blockfrost import ApiUrls  # noqa: E402
from pycardano import BlockFrostChainContext  # noqa: E402
from charli3_dendrite.backend.blockfrost import BlockFrostBackend  # noqa: E402


def _patched_bfb_init(self, project_id: str) -> None:
    """Pick base URL from project_id prefix (preprod / preview / mainnet)."""
    if project_id.startswith("preprod"):
        base_url = ApiUrls.preprod.value
    elif project_id.startswith("preview"):
        base_url = ApiUrls.preview.value
    else:
        base_url = ApiUrls.mainnet.value
    self.chain_context = BlockFrostChainContext(project_id, base_url=base_url)
    self.api = self.chain_context.api
    self._block_cache = {}


BlockFrostBackend.__init__ = _patched_bfb_init

if _saved_pid is not None:
    os.environ["BLOCKFROST_PROJECT_ID"] = _saved_pid


# 3. Route pycardano's evaluate_tx_cbor through Ogmios. Blockfrost's
#    /utils/txs/evaluate collapses empty ScriptFailures into Namespace()
#    which hides real Plutus errors AND fails our valid txs upstream.
#    This is the same workaround as scripts/odv-first-round.py:48-109.
import json as _json  # noqa: E402
import urllib.request  # noqa: E402

from pycardano import ExecutionUnits  # noqa: E402
from pycardano.backend import blockfrost as _pbf  # noqa: E402

_OGMIOS_URL = os.environ.get("OGMIOS_URL", "http://35.209.192.203:1337").rstrip("/")


def _ogmios_evaluate(self, cbor):
    """Replace BlockFrostChainContext.evaluate_tx_cbor with an Ogmios JSON-RPC call."""
    cbor_hex = cbor.hex() if isinstance(cbor, (bytes, bytearray)) else cbor

    payload = _json.dumps({
        "jsonrpc": "2.0",
        "method": "evaluateTransaction",
        "params": {"transaction": {"cbor": cbor_hex}},
        "id": "c3-supply-node",
    }).encode("utf-8")

    req = urllib.request.Request(
        _OGMIOS_URL,
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = _json.load(resp)
    except urllib.error.HTTPError as e:
        body = _json.loads(e.read().decode("utf-8", errors="replace"))

    if "error" in body:
        raise RuntimeError(f"Ogmios evaluate error: {body['error']}")

    out = {}
    for entry in body.get("result", []):
        purpose = entry["validator"]["purpose"]
        index = entry["validator"]["index"]
        out[f"{purpose}:{index}"] = ExecutionUnits(
            entry["budget"]["memory"], entry["budget"]["cpu"]
        )
    return out


_pbf.BlockFrostChainContext.evaluate_tx_cbor = _ogmios_evaluate
