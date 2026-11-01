import logging

from fastapi import APIRouter, Request, Response, Security

from ..auth import api_key_scheme
from ..logging import request_id_var
from ..models import AnalysisRequest, AnalysisResponse, ErrorResponse
from ..stub import build_stub_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Analyses"], dependencies=[Security(api_key_scheme)])

_ERROR_STATUSES = (400, 401, 409, 415, 422, 429, 500, 503, 504)


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
    response.headers["X-MAI-Stub"] = "true"
    return build_stub_analysis(body, request.app.state.settings)
