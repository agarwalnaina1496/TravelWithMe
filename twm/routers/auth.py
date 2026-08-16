"""Account signup and login HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import AuthService, InvalidCredentialsError
from ..dependencies import get_auth_service
from ..persistence.contracts import DuplicateEmailError
from ..schemas.auth import LoginRequest, LoginResponse, SignupRequest, SignupResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
Auth = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(payload: SignupRequest, auth: Auth):
    try:
        user = await auth.signup(payload.email, payload.password)
    except DuplicateEmailError as error:
        raise HTTPException(status_code=409, detail="Email is already registered.") from error
    return SignupResponse(id=user.id, email=user.email, created_at=user.created_at)


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response, auth: Auth):
    try:
        user = await auth.login(payload.email, payload.password, response)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=401, detail="Incorrect email or password.") from error
    return LoginResponse(id=user.id, email=user.email)
