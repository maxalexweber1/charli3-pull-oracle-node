"""ODV protocol endpoints.

Multi-feed (D-05): each endpoint takes `feed_id` as a path parameter to
dispatch to the right OdvService. Legacy single-feed callers should use
feed_id="default".
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from node.api.dependencies import (
    get_any_odv_service,
    get_odv_service_by_feed,
)
from node.api.schemas.requests import (
    NodeAggregationSignRequest,
    NodeFeedRequest,
    OdvAggregateRequest,
)
from node.api.schemas.responses import (
    NodeAggregationSignResponse,
    NodeFeedResponse,
    OdvAggregateResponse,
)
from node.core.errors import NodeServiceError
from node.core.odv import OdvService

router = APIRouter()


@router.post("/feed/{feed_id}", response_model=NodeFeedResponse)
async def get_feed(
    feed_id: str,
    request: NodeFeedRequest,
    odv_service: OdvService = Depends(get_odv_service_by_feed),
):
    """Handle ODV feed value request for a specific feed."""
    try:
        response = await odv_service.handle_feed_request(
            request.oracle_nft_policy_id, request.tx_validity_interval
        )
        return NodeFeedResponse.model_validate(response.model_dump())
    except NodeServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "detail": str(e),
                "error_code": e.status_code,
                "error_type": e.__class__.__name__,
            },
        )


@router.post("/aggregate/{feed_id}", response_model=OdvAggregateResponse)
async def aggregate(
    feed_id: str,
    request: OdvAggregateRequest,
    odv_service: OdvService = Depends(get_odv_service_by_feed),
):
    """Coordinator-mode orchestration for one feed (D-05 fork).

    The node that receives this call is the coordinator for this round:
    it collects signed feed messages from its peers, builds + signs +
    submits the aggregation Tx, and returns the resulting tx hash.
    """
    try:
        result = await odv_service.handle_aggregate_request(
            oracle_nft_policy_id=request.oracle_nft_policy_id,
            peer_urls=request.peer_urls,
            tx_validity_interval=request.tx_validity_interval,
        )
        return OdvAggregateResponse(**result)
    except NodeServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "detail": str(e),
                "error_code": e.status_code,
                "error_type": e.__class__.__name__,
            },
        )


@router.post("/sign/{feed_id}", response_model=NodeAggregationSignResponse)
async def sign_aggregation(
    request: Request,
    feed_id: str,
    signature_request: NodeAggregationSignRequest,
    odv_service: OdvService = Depends(get_odv_service_by_feed),
):
    """Handle ODV aggregation transaction signing for a specific feed."""
    try:
        signature_hex = await odv_service.handle_aggregation_sign_request(
            signature_request.node_messages, signature_request.tx_body_cbor
        )

        return NodeAggregationSignResponse(signature=signature_hex)
    except NodeServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "detail": str(e),
                "error_code": e.status_code,
                "error_type": e.__class__.__name__,
            },
        )


# Legacy single-feed endpoints (unversioned) — dispatch to the first
# registered service. Kept for backward compatibility; new callers should
# use the /feed/{feed_id} + /sign/{feed_id} endpoints.
@router.post("/feed", response_model=NodeFeedResponse, include_in_schema=False)
async def get_feed_legacy(
    request: NodeFeedRequest,
    odv_service: OdvService = Depends(get_any_odv_service),
):
    try:
        response = await odv_service.handle_feed_request(
            request.oracle_nft_policy_id, request.tx_validity_interval
        )
        return NodeFeedResponse.model_validate(response.model_dump())
    except NodeServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "detail": str(e),
                "error_code": e.status_code,
                "error_type": e.__class__.__name__,
            },
        )


@router.post(
    "/sign", response_model=NodeAggregationSignResponse, include_in_schema=False
)
async def sign_aggregation_legacy(
    request: Request,
    signature_request: NodeAggregationSignRequest,
    odv_service: OdvService = Depends(get_any_odv_service),
):
    try:
        signature_hex = await odv_service.handle_aggregation_sign_request(
            signature_request.node_messages, signature_request.tx_body_cbor
        )
        return NodeAggregationSignResponse(signature=signature_hex)
    except NodeServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "detail": str(e),
                "error_code": e.status_code,
                "error_type": e.__class__.__name__,
            },
        )
