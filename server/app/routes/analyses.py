import logging

from fastapi import APIRouter, Request, Response, Security

from ..auth import api_key_scheme
from ..idempotency import IdempotencyStore
from ..logging import request_id_var
from ..models import AnalysisRequest, AnalysisResponse, ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Analyses"], dependencies=[Security(api_key_scheme)])

_ERROR_STATUSES = (400, 401, 409, 415, 422, 429, 500, 503, 504)


def _replay(stored: AnalysisResponse, response: Response) -> AnalysisResponse:
    logger.info("analysis replayed from stored result")
    response.headers["X-MAI-Replayed"] = "true"
    return stored


@router.post(
    "/analyses",
    operation_id="createAnalysis",
    summary="Analyze a reading recording (synchronous)",
    response_model=AnalysisResponse,
    responses={status: {"model": ErrorResponse} for status in _ERROR_STATUSES},
)
async def create_analysis(
    body: AnalysisRequest, request: Request, response: Response
) -> AnalysisResponse:
    request_id_var.set(str(body.request_id))
    logger.info(
        "analysis received",
        extra={
            "passage_title_length": len(body.passage_title),
            "video_host": body.video_url.host,
        },
    )
    response.headers["X-MAI-Stub"] = "true"  # Phase 4 removes this

    store: IdempotencyStore = request.app.state.idempotency
    stored = await store.lookup(body)
    if stored is not None:
        return _replay(stored, response)

    # Phase 6: admission control (429 when saturated) slots in here, before claiming.

    claimed = await store.claim(body)
    if isinstance(claimed, AnalysisResponse):  # another request finished while we were claiming
        return _replay(claimed, response)

    try:
        result = await request.app.state.processor(body, request.app.state.settings)
    except BaseException:  # includes cancellation; errors are never cached
        await store.release(body, claimed)
        raise
    await store.complete(body, claimed, result)
    return result
