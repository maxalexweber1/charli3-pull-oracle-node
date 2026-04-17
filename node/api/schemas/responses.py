from pydantic import BaseModel, Field


class NodeFeedResponse(BaseModel):
    """Response containing signed feed value."""

    message: str = Field(..., description="Signed feed message CBOR hex")
    signature: str = Field(..., description="Signature hex")
    verification_key: str = Field(..., description="Verification key hex")


class NodeAggregationSignResponse(BaseModel):
    """Response containing signature"""

    signature: str = Field(..., description="Transaction signature hex")


class OdvAggregateResponse(BaseModel):
    """Coordinator response from /odv/aggregate/{feed_id}."""

    tx_hash: str = Field(..., description="Submitted aggregation Tx hash")
    feed_value: int = Field(..., description="Median feed value submitted on-chain")
    timestamp_ms: int = Field(..., description="Creation timestamp in ms")
    peers_responded: int = Field(..., description="Peer nodes that returned a signed feed message")
    status: str = Field(default="submitted", description="Submission status")
