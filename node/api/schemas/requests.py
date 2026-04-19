from charli3_offchain_core.models.base import TxValidityInterval
from pydantic import BaseModel, Field


class NodeFeedRequest(BaseModel):
    """Request for oracle feed value"""

    oracle_nft_policy_id: str = Field(..., description="Oracle NFT policy ID")
    tx_validity_interval: TxValidityInterval = Field(
        ...,
        description="Transaction validity interval containing start and end timestamps",
    )


class NodeMessage(BaseModel):
    """Node message"""

    message: str = Field(..., description="Node message CBOR hex")
    signature: str = Field(..., description="Signature hex")
    verification_key: str = Field(..., description="Verification key hex")


class NodeAggregationSignRequest(BaseModel):
    """Request to sign transaction"""

    node_messages: dict[str, NodeMessage] = Field(
        ..., description="Participating node messages"
    )
    tx_body_cbor: str = Field(..., description="Transaction Body CBOR hex")


class ChainedUtxoRef(BaseModel):
    """A UTxO transport format for tx-chaining across back-to-back aggregations.

    Carries CBOR-hex of both the TransactionInput and the TransactionOutput
    of a previously-built-but-not-yet-confirmed aggregation tx, so the next
    coordinator can reconstruct the pycardano UTxO without a chain query.
    """

    input_cbor: str = Field(..., description="TransactionInput CBOR hex")
    output_cbor: str = Field(..., description="TransactionOutput CBOR hex")


class OdvAggregateRequest(BaseModel):
    """Orchestrator request (D-05): coordinator node fans out to peer nodes,
    collects signed feed messages, builds the aggregation Tx, signs with all
    node feed keys, and submits.
    """

    oracle_nft_policy_id: str = Field(..., description="Oracle NFT policy ID")
    peer_urls: list[str] = Field(
        default_factory=list,
        description=(
            "HTTP base URLs of peer nodes (each exposing /odv/feed/{feed_id}). "
            "The coordinator's own feed is fetched locally — do not include it here."
        ),
    )
    tx_validity_interval: TxValidityInterval = Field(
        ..., description="Validity interval for the ODV aggregation round"
    )
    reward_account_utxo_override: ChainedUtxoRef | None = Field(
        default=None,
        description=(
            "Optional: skip the on-chain lookup for the RewardAccount UTxO and "
            "use the supplied one instead. Enables tx-chaining: when inventory "
            "and price feeds of one oracle submit back-to-back, the price tx "
            "can consume the just-built (but not yet confirmed) RewardAccount "
            "output of the inventory tx directly, eliminating the BadInputsUTxO "
            "race caused by the shared C3RA UTxO."
        ),
    )
