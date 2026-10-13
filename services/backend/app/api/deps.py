"""FastAPI dependencies."""

from __future__ import annotations

import re

from fastapi import Request
from marketlens_core.errors import ErrorCode, MarketLensError

from app.container import AppContainer

RUN_ID_RE = re.compile(r"^run_[0-9a-f]{16}$")
BRIEF_ID_RE = re.compile(r"^brief_[0-9a-f]{16}$")


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_session_id(request: Request) -> str:
    return request.state.session_id


def valid_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
    return run_id


def valid_brief_id(brief_id: str) -> str:
    if not BRIEF_ID_RE.fullmatch(brief_id):
        raise MarketLensError(ErrorCode.NOT_FOUND, "Saved brief not found.")
    return brief_id
