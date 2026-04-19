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
#
#    Tx-chaining: when the SDK builds a tx that spends a not-yet-confirmed
#    UTxO (e.g. a C3RA output chained from a previous aggregation), the
#    script context on Ogmios's current ledger state can't resolve that
#    input and the evaluate fails with code 3010. The SDK registers the
#    chained UTxO thread-local via `charli3_offchain_core._chained_utxos`
#    so we can forward it here as `additionalUtxo`.
import json as _json  # noqa: E402
import logging as _logging  # noqa: E402
import urllib.request  # noqa: E402

from pycardano import ExecutionUnits  # noqa: E402
from pycardano.backend import blockfrost as _pbf  # noqa: E402

_OGMIOS_URL = os.environ.get("OGMIOS_URL", "http://35.209.192.203:1337").rstrip("/")
_ogmios_log = _logging.getLogger(__name__)


def _utxo_to_ogmios_additional_utxo(utxo):
    """Convert a pycardano UTxO to Ogmios v6 additionalUtxo entry.

    Ogmios v6 Utxo schema: flat objects with transaction+index+address+value+
    optional datum/datumHash/script — NOT [input, output] tuples (that was v5).
    """
    out = utxo.output
    value = {"ada": {"lovelace": int(out.amount.coin)}}
    if out.amount.multi_asset:
        for policy, assets in out.amount.multi_asset.data.items():
            policy_hex = policy.payload.hex() if hasattr(policy, "payload") else bytes(policy).hex()
            asset_map = {}
            for asset, count in assets.data.items():
                asset_hex = asset.payload.hex() if hasattr(asset, "payload") else bytes(asset).hex()
                asset_map[asset_hex] = int(count)
            if asset_map:
                value[policy_hex] = asset_map

    # pycardano TransactionId: prefer .payload (raw bytes) → hex. str() returns
    # a Python repr ("TransactionId(hex='…')") which Ogmios can't parse.
    tx_id = utxo.input.transaction_id
    if hasattr(tx_id, "payload"):
        tx_id_hex = tx_id.payload.hex()
    else:
        tx_id_hex = bytes(tx_id).hex()

    entry = {
        "transaction": {"id": tx_id_hex},
        "index": int(utxo.input.index),
        "address": str(out.address),
        "value": value,
    }
    if out.datum is not None:
        try:
            datum_cbor = out.datum.to_cbor().hex() if hasattr(out.datum, "to_cbor") else bytes(out.datum).hex()
            entry["datum"] = datum_cbor
        except Exception as e:
            _ogmios_log.warning("Failed to serialize datum for additionalUtxo: %s", e)
    return entry


def _ogmios_evaluate(self, cbor, additional_utxos=None):
    """Replace BlockFrostChainContext.evaluate_tx_cbor with an Ogmios JSON-RPC call.

    Accepts both the pycardano-passed `additional_utxos` kwarg and pulls in
    any thread-local chained UTxOs registered by the SDK, so script-context
    resolution covers virtual (unconfirmed) inputs.
    """
    cbor_hex = cbor.hex() if isinstance(cbor, (bytes, bytearray)) else cbor

    # Merge explicit kwarg with SDK thread-local registration.
    try:
        from charli3_offchain_core import _chained_utxos  # noqa: WPS433
        registered = _chained_utxos.get_current()
    except Exception:
        registered = []
    merged = list(additional_utxos or []) + list(registered or [])

    params: dict = {"transaction": {"cbor": cbor_hex}}
    if merged:
        try:
            params["additionalUtxo"] = [
                _utxo_to_ogmios_additional_utxo(u) for u in merged
            ]
            _ogmios_log.info(
                "Forwarding %d chained UTxO(s) as Ogmios additionalUtxo", len(merged)
            )
        except Exception as e:
            _ogmios_log.warning(
                "Failed to build additionalUtxo; evaluating without chain context: %s", e
            )

    payload = _json.dumps({
        "jsonrpc": "2.0",
        "method": "evaluateTransaction",
        "params": params,
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
