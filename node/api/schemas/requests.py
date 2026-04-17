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
