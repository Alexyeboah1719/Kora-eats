import logging

from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["security"])
logger = logging.getLogger("app.csp")


@router.post("/csp-report", include_in_schema=False)
async def csp_report(request: Request):
    """Browsers POST Content-Security-Policy violations here. We log them so the policy can be tightened safely."""
    body = (await request.body())[:4000]          # never trust or store unbounded input
    logger.warning("CSP violation: %s", body.decode("utf-8", "replace").replace("\n", " "))
    return Response(status_code=204)
