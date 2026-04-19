from pydantic import BaseModel, Field


class NodeFeedResponse(BaseModel):
    """Response containing signed feed value."""

    message: str = Field(..., description="Signed feed message CBOR hex")
    signature: str = Field(..., description="Signature hex")
    verification_key: str = Field(..., description="Verification key hex")


class NodeAggregationSignResponse(BaseModel):
    """Response containing signature"""

    signature: str = Field(..., description="Transaction signature hex")


class ChainedUtxoRef(BaseModel):
    """A UTxO transport format for tx-chaining — see request schema."""

    input_cbor: str = Field(..., description="TransactionInput CBOR hex")
    output_cbor: str = Field(..., description="TransactionOutput CBOR hex")


class OdvAggregateResponse(BaseModel):
    """Coordinator response from /odv/aggregate/{feed_id}."""

    tx_hash: str = Field(..., description="Submitted aggregation Tx hash")
    feed_value: int = Field(..., description="Median feed value submitted on-chain")
    timestamp_ms: int = Field(..., description="Creation timestamp in ms")
    peers_responded: int = Field(..., description="Peer nodes that returned a signed feed message")
    status: str = Field(default="submitted", description="Submission status")
    new_reward_account_utxo: ChainedUtxoRef | None = Field(
        default=None,
        description=(
            "The new RewardAccount (C3RA) UTxO produced by this submission. "
            "Caller should pass this as `reward_account_utxo_override` in the "
            "next back-to-back aggregation for the same policy to avoid the "
            "shared-C3RA race."
        ),
    )
