"""内置浏览器 API：网页代理、阅读模式、预览票据。

前端把 iframe 指向 `/api/browse/web?url=...&ticket=...`，拿到的就是已经
去掉 frame 限制、补好 base 的页面；`/api/browse/read` 走正文抽取，
用于代理失败或用户主动切阅读模式时。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app.api.deps import current_user, current_workspace
from app.services import browser, tickets
from app.services.capabilities import deck, web
from app.services.users import User

router = APIRouter(prefix="/browse", tags=["browse"])


@router.get("/ticket")
async def issue_ticket(
    user: User = Depends(current_user),
    ws: str = Depends(current_workspace),
) -> dict:
    """给 iframe / img 用的短期票据（15 分钟，只放行预览类 GET）。"""
    return tickets.issue(user.id, ws)


@router.get("/web")
async def proxy(url: str = Query(min_length=3)) -> Response:
    try:
        body, ctype, final_url = browser.frame_payload(url)
    except web.WebError as exc:
        return HTMLResponse(
            browser.error_page(url, str(exc)),
            status_code=502,
            headers={"X-Final-Url": quote(url, safe="")},
        )
    headers = {
        # 跳转后的真实地址回传前端，地址栏才显示得对
        "X-Final-Url": quote(final_url, safe=""),
        "Cache-Control": "no-store",
    }
    return Response(content=body, media_type=ctype, headers=headers)


@router.get("/read")
async def read(url: str = Query(min_length=3)) -> dict:
    try:
        return web.read_page(url)
    except web.WebError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/themes")
async def deck_themes() -> list[dict]:
    """PPT 编辑器的主题色板。放在 /browse 下是为了避开 /projects/{project} 的路径吞并。"""
    return deck.list_themes()
