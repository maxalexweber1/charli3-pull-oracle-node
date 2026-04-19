import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
import charli3_offchain_core.oracle.aggregate.builder as odv_builder
from charli3_offchain_core.blockchain.chain_query import ChainQuery
from charli3_offchain_core.blockchain.transactions import TransactionManager
from charli3_offchain_core.cli.base import LoadedKeys
from charli3_offchain_core.cli.config.reference_script import ReferenceScriptConfig
from charli3_offchain_core.models.base import TxValidityInterval
from charli3_offchain_core.models.message import (
    OracleNodeMessage,
    SignedOracleNodeMessage,
)
from charli3_offchain_core.models.oracle_datums import (
    Asset,
    NoDatum,
    RewardAccountVariant,
    SomeAsset,
)
from charli3_offchain_core.models.oracle_redeemers import AggregateMessage
from charli3_offchain_core.oracle.exceptions import (
    AggregationError,
    DataError,
    NFTError,
    NodeValidationError,
    SignatureError,
    StateValidationError,
    TimestampError,
)
from charli3_offchain_core.oracle.rewards.node_collect_builder import NodeCollectBuilder
from charli3_offchain_core.oracle.validations.aggregation import (
    validate_is_node_registered,
    validate_node_message_signatures,
    validate_node_updates_and_aggregation_median,
    validate_policy_id_in_messages,
    validate_timestamp,
    validate_transaction_datums,
)
from pycardano import (
    Address,
    AssetName,
    ExtendedSigningKey,
    PaymentExtendedSigningKey,
    PaymentVerificationKey,
    ScriptHash,
    Transaction,
    TransactionBody,
    TransactionInput,
    TransactionOutput,
    TransactionWitnessSet,
    UTxO,
    VerificationKey,
    VerificationKeyHash,
)

from node.core.aggregator import RateAggregator
from node.core.errors import NodeServiceError, RateAggregationError, ValidationError
from node.services.cli_automation import create_reward_collection_automation

logger = logging.getLogger(__name__)


class OdvService:
    """Service handling ODV (On-Demand Validation) operations"""

    def __init__(
        self,
        rate_aggregator: RateAggregator,
        chain_query: ChainQuery,
        tx_manager: TransactionManager,
        oracle_addr: str,
        oracle_curr: str,
        node_feed_sk: ExtendedSigningKey,
        node_feed_vk: VerificationKey,
        node_feed_vkh: VerificationKeyHash,
        node_payment_sk: PaymentExtendedSigningKey,
        node_payment_vk: PaymentVerificationKey,
        reward_token_hash: str | None = None,
        reward_token_name: str | None = None,
        reward_destination_address: str | None = None,
        create_collateral: bool = True,
        ref_script_config: ReferenceScriptConfig | None = None,
        aggstate_asset_name: str = "C3AS",
    ):
        self.rate_aggregator = rate_aggregator
        self.chain_query = chain_query
        self.ref_script_config = ref_script_config
        self.tx_manager = tx_manager
        self.oracle_addr = oracle_addr
        self.oracle_curr = oracle_curr
        self.reward_token_hash = reward_token_hash
        self.reward_token_name = reward_token_name
        self.reward_destination_address = reward_destination_address
        self.create_collateral = create_collateral
        self.aggstate_asset_name = aggstate_asset_name
        self.node_feed_sk = node_feed_sk
        self.node_feed_vk = node_feed_vk
        self.node_feed_vkh = node_feed_vkh
        self.node_payment_sk = node_payment_sk
        self.node_payment_vk = node_payment_vk
        self.network = self.chain_query.context.network
        self.node_payment_addr = Address(
            payment_part=self.node_payment_vk.hash(), network=self.network
        )

        self.oracle_script_address = Address.from_primitive(oracle_addr)
        self.oracle_policy_id = ScriptHash.from_primitive(oracle_curr)
        self.reward_token_policy_hash = (
            ScriptHash.from_primitive(reward_token_hash) if reward_token_hash else None
        )
        self.reward_token_asset_name = (
            AssetName.from_primitive(reward_token_name) if reward_token_name else None
        )

        self.odv_tx_builder = odv_builder.OracleTransactionBuilder(
            tx_manager=self.tx_manager,
            script_address=self.oracle_script_address,
            policy_id=self.oracle_policy_id,
            reward_token_hash=self.reward_token_policy_hash,
            reward_token_name=self.reward_token_asset_name,
            ref_script_config=ref_script_config,
            aggstate_asset_name=aggstate_asset_name,
        )

    async def handle_feed_request(
        self, oracle_nft_policy_id: str, tx_validity_interval: TxValidityInterval
    ) -> SignedOracleNodeMessage:
        """Handle ODV feed request"""
        try:
            timestamp = self.chain_query.get_current_posix_chain_time_ms()

            validate_timestamp(tx_validity_interval.model_dump(), timestamp)

            await validate_is_node_registered(
                self.tx_manager,
                self.oracle_addr,
                oracle_nft_policy_id,
                self.node_feed_vk.hash(),
            )

            # Get and process rate
            rate, _ = await self.rate_aggregator.fetch_aggregate_rates()
            if rate is None:
                raise RateAggregationError("Failed to get aggregated rate for ODV feed")

            # Create message
            message = OracleNodeMessage(
                feed=int(rate * 1_000_000),
                timestamp=timestamp,
                oracle_nft_policy_id=bytes.fromhex(oracle_nft_policy_id),
            )

            # Sign message and return signature
            signature = message.sign(self.node_feed_sk)

            signed_message = SignedOracleNodeMessage(
                message=message,
                signature=signature,
                verification_key=self.node_feed_vk,
            )
            return signed_message

        except (NodeServiceError, Exception) as e:
            logger.error(str(e))
            raise

    async def handle_aggregation_sign_request(
        self, node_values: dict[str, Any], tx_body_cbor_hex: str
    ) -> str:
        """Handle ODV aggregation transaction signing."""
        try:

            tx_body_cbor_bytes = bytes.fromhex(tx_body_cbor_hex)
            tx_body_hash_bytes = hashlib.blake2b(
                tx_body_cbor_bytes, digest_size=32
            ).digest()
            # tx_body_hash_hex = tx_body_hash_bytes.hex()

            # Deserialize transaction body for validation purposes only
            parsed_tx_body = TransactionBody.from_cbor(tx_body_cbor_hex)

            validation_tx = Transaction(
                transaction_body=parsed_tx_body,
                transaction_witness_set=TransactionWitnessSet(),
            )

            # Validates the node message signatures
            validated_node_messages = validate_node_message_signatures(
                [msg.model_dump() for msg in node_values.values()]
            )

            # Validate policy ID consistency
            validate_policy_id_in_messages(validated_node_messages)

            # Validate and extract required datums from the transaction.
            reward_account_datum, agg_state_datum = validate_transaction_datums(
                validation_tx, self.oracle_addr
            )

            # Validate median using AggState datum
            median_validation_passed = validate_node_updates_and_aggregation_median(
                validated_node_messages, agg_state_datum
            )

            if not median_validation_passed:
                raise ValidationError(
                    "Node updates and aggregation median validation failed"
                )

            logger.info("All validations passed successfully")

            # Sign the transaction body hash
            signature_bytes = self.node_feed_sk.sign(tx_body_hash_bytes)
            signature_hex = signature_bytes.hex()

            logger.info(
                f"Transaction signed successfully with signature: {signature_hex}"
            )

            return signature_hex

        except (
            SignatureError,
            NFTError,
            DataError,
            AggregationError,
            NodeValidationError,
            StateValidationError,
            TimestampError,
        ) as e:
            logger.warning(f"Validation failed during aggregation signing: {e}")
            raise ValidationError(str(e)) from e
        except Exception as e:
            logger.error(f"Aggregation sign request failed: {str(e)}")
            raise

    async def handle_aggregate_request(
        self,
        oracle_nft_policy_id: str,
        peer_urls: list[str],
        tx_validity_interval: TxValidityInterval,
        reward_account_utxo_override_cbor: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Coordinator-mode ODV orchestration (D-05).

        The node running this method is the coordinator for one aggregation
        round of one feed. It:
          1. Produces its own signed feed message locally.
          2. Fans /odv/feed/{feed_id} to peer nodes in parallel.
          3. Builds an `AggregateMessage` sorted by (feed_value, vkh).
          4. Builds the aggregation Tx via `OracleTransactionBuilder` with
             `aggstate_asset_name` = `self.aggstate_asset_name` (which the
             gepatcht `state_checks.find_account_pair` uses to pick the
             right AggState UTxO under the shared policy).
          5. Signs the Tx with the coordinator's payment key AND all node
             feed signing keys it can load from `COORDINATOR_KEYS_DIR`
             (plus its own key).
          6. Submits via tx_manager.sign_and_submit.

        `COORDINATOR_KEYS_DIR` is optional env: if unset, only the
        coordinator's own node_feed_sk signs — submit will fail the
        threshold check unless the coordinator happens to be the only
        signer required.
        """
        feed_id = getattr(self, "feed_id", "default")

        # (1) Coordinator's own feed message.
        self_signed = await self.handle_feed_request(
            oracle_nft_policy_id, tx_validity_interval
        )

        # (2) Peer feed messages (parallel, best-effort).
        peer_signed = await _collect_peer_feed_messages(
            peer_urls, feed_id, oracle_nft_policy_id, tx_validity_interval
        )
        all_signed = [self_signed, *peer_signed]

        # (3) Build AggregateMessage. pycardano returns VerificationKey; hash to VKH.
        feeds: dict[VerificationKeyHash, int] = {}
        for sm in all_signed:
            vkh = sm.verification_key.hash()
            feeds[vkh] = sm.message.feed
        # Sort by (feed_value, vkh_bytes) as the core-lib builder expects.
        sorted_feeds = dict(
            sorted(feeds.items(), key=lambda kv: (kv[1], kv[0].payload))
        )
        aggregate_msg = AggregateMessage(node_feeds_sorted_by_feed=sorted_feeds)

        # (4) Build tx. `OracleTransactionBuilder` was constructed with
        # `aggstate_asset_name` from this service's feed config (main.py loop).
        # If a tx-chaining override was passed in, decode the UTxO and use it
        # instead of the on-chain RewardAccount lookup — lets this tx spend
        # the new C3RA output of a previous back-to-back aggregation before
        # that tx is ledger-confirmed.
        override_utxo: UTxO | None = None
        if reward_account_utxo_override_cbor is not None:
            try:
                inp_hex, out_hex = reward_account_utxo_override_cbor
                override_utxo = UTxO(
                    TransactionInput.from_cbor(bytes.fromhex(inp_hex)),
                    TransactionOutput.from_cbor(bytes.fromhex(out_hex)),
                )
                # Re-type the inline datum from RawPlutusData back to
                # RewardAccountVariant so the SDK's _create_reward_account_output
                # can call `.datum.nodes_to_rewards` on it. Pycardano's generic
                # CBOR decoder returns RawPlutusData for any datum without a
                # registered schema; the SDK expects the typed variant.
                if override_utxo.output.datum is not None:
                    try:
                        raw_datum_cbor = override_utxo.output.datum.to_cbor()
                        override_utxo.output.datum = RewardAccountVariant.from_cbor(
                            raw_datum_cbor
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to retype override datum to RewardAccountVariant: %s", e
                        )
                logger.info(
                    "Using chained RewardAccount UTxO override: %s#%d",
                    override_utxo.input.transaction_id,
                    override_utxo.input.index,
                )
            except Exception as e:
                logger.warning(
                    "Failed to decode reward_account_utxo_override_cbor: %s "
                    "-- falling back to on-chain lookup", e
                )
                override_utxo = None

        result = await self.odv_tx_builder.build_odv_tx(
            message=aggregate_msg,
            signing_key=self.node_payment_sk,
            change_address=self.node_payment_addr,
            reward_account_utxo_override=override_utxo,
        )

        # (5) Collect signing keys: coordinator's payment key + all available
        # node feed skeys (coordinator's own + any in COORDINATOR_KEYS_DIR).
        all_keys: list = [self.node_payment_sk, self.node_feed_sk]
        extra_keys_dir = os.environ.get("COORDINATOR_KEYS_DIR")
        if extra_keys_dir:
            for skey_path in sorted(Path(extra_keys_dir).glob("*.skey")):
                try:
                    extra = ExtendedSigningKey.load(str(skey_path))
                except Exception:
                    try:
                        extra = PaymentExtendedSigningKey.load(str(skey_path))
                    except Exception as e:
                        logger.warning(
                            "Failed to load %s: %s", skey_path, e
                        )
                        continue
                # Skip our own feed key (already in list).
                try:
                    if extra.to_verification_key().hash() == self.node_feed_vkh:
                        continue
                except Exception:
                    pass
                all_keys.append(extra)

        # (6) Sign + submit. wait_confirmation=False so this endpoint returns
        # quickly; the caller polls the chain (via bridge /feeds) for
        # confirmation.
        status, _submitted = await self.tx_manager.sign_and_submit(
            result.transaction, all_keys, wait_confirmation=False
        )

        feed_values = sorted(sorted_feeds.values())
        median = feed_values[len(feed_values) // 2] if feed_values else 0

        new_ra_dict: dict[str, str] | None = None
        if result.new_reward_account_utxo is not None:
            try:
                new_ra_dict = {
                    "input_cbor": result.new_reward_account_utxo.input.to_cbor().hex(),
                    "output_cbor": result.new_reward_account_utxo.output.to_cbor().hex(),
                }
            except Exception as e:
                logger.warning("Failed to serialize new RewardAccount UTxO: %s", e)

        return {
            "tx_hash": str(result.transaction.id),
            "feed_value": median,
            "timestamp_ms": int(time.time() * 1000),
            "peers_responded": len(peer_signed),
            "status": status,
            "new_reward_account_utxo": new_ra_dict,
        }

    async def attempt_node_collect(self, contract_utxos: list | None = None) -> None:
        """
        Build node collect tx and attempt submitting it,
        if the process fails there could be cases when we can be sure that it's normal:
        1. No rewards available for the node
        2. Transaction submission error (can happen when e.g. utxo already consumed by other node)
        3. Validation error: conditions for node collect have not been yet met
        """
        try:
            if contract_utxos is None:
                return

            loaded_keys = LoadedKeys(
                payment_sk=self.node_payment_sk,
                payment_vk=self.node_feed_vk,
                stake_vk=self.node_payment_vk,
                address=self.node_payment_addr,
            )

            # Create NodeCollectBuilder and build the transaction
            node_collect_builder = NodeCollectBuilder(self.chain_query, self.tx_manager)

            # Use automated prompts if configured
            if self.reward_destination_address:
                # Create automation service for reward collection
                automation_service = create_reward_collection_automation(
                    create_collateral=self.create_collateral,
                    reward_destination=self.reward_destination_address,
                )

                with automation_service.automate_prompts():
                    result = await node_collect_builder.build_tx(
                        policy_hash=self.oracle_policy_id,
                        contract_utxos=contract_utxos,
                        reward_token=(
                            SomeAsset(
                                Asset(
                                    policy_id=self.reward_token_policy_hash.payload,
                                    name=self.reward_token_asset_name.payload,
                                )
                            )
                            if self.reward_token_policy_hash
                            and self.reward_token_asset_name
                            else NoDatum()
                        ),
                        loaded_key=loaded_keys,
                        script_address=self.oracle_script_address,
                        ref_script_config=self.ref_script_config,
                        network=self.network,
                        required_signers=[
                            self.node_feed_vk.hash(),
                        ],
                    )

                # Check if transaction was built successfully
                if result.exception_type is not None:
                    logger.warning(
                        f"Node collect transaction build failed: {result.exception_type}"
                    )
                    return

                if result.transaction is None:
                    logger.warning(
                        "Node collect transaction build returned no transaction"
                    )
                    return

                # Submit the transaction
                status, _ = await self.tx_manager.sign_and_submit(
                    result.transaction,
                    [self.node_feed_sk, self.node_payment_sk],
                    wait_confirmation=True,
                )
                if status != "confirmed":
                    logger.warning(f"Node collect transaction failed: {status}")
                else:
                    logger.info(
                        f"Node collect transaction submitted: {result.transaction.id.payload.hex()}"
                    )

        except Exception as e:
            logger.error(f"Node collect failed: {e}", exc_info=e)


async def _collect_peer_feed_messages(
    peer_urls: list[str],
    feed_id: str,
    oracle_nft_policy_id: str,
    tx_validity_interval: TxValidityInterval,
) -> list[SignedOracleNodeMessage]:
    """Fan out /odv/feed/{feed_id} to peer nodes in parallel.

    Peer errors are swallowed (logged). Caller is responsible for the
    threshold check; this function only returns what it successfully got.
    """
    if not peer_urls:
        return []

    payload = {
        "oracle_nft_policy_id": oracle_nft_policy_id,
        "tx_validity_interval": tx_validity_interval.model_dump(),
    }
    timeout = aiohttp.ClientTimeout(total=30)

    async def fetch_one(url: str) -> SignedOracleNodeMessage | None:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{url.rstrip('/')}/odv/feed/{feed_id}", json=payload
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Peer %s /odv/feed/%s returned %s",
                            url, feed_id, resp.status,
                        )
                        return None
                    data = await resp.json()
                    return SignedOracleNodeMessage.model_validate(data)
        except Exception as e:
            logger.warning("Peer %s unreachable: %s", url, e)
            return None

    results = await asyncio.gather(
        *[fetch_one(u) for u in peer_urls], return_exceptions=False
    )
    return [r for r in results if r is not None]
