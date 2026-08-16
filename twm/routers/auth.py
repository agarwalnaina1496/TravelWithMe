"""Account signup and login HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import AuthService, InvalidCredentialsError
from ..dependencies import get_auth_service, get_current_user
from ..persistence.contracts import DuplicateEmailError, User
from ..schemas.auth import LoginRequest, LoginResponse, MeResponse, SignupRequest, SignupResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
Auth = Annotated[AuthService, Depends(get_auth_service)]
CurrentUser = Annotated[User | None, Depends(get_current_user)]


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(payload: SignupRequest, request: Request, auth: Auth):
    try:
        result = await auth.signup(payload.email, payload.password, request)
    except DuplicateEmailError as error:
        raise HTTPException(status_code=409, detail="Email is already registered.") from error
    return SignupResponse(
        id=result.user.id, email=result.user.email, created_at=result.user.created_at,
        claimed_trip_count=result.claimed_trip_count,
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response, auth: Auth):
    try:
        result = await auth.login(payload.email, payload.password, request, response)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=401, detail="Incorrect email or password.") from error
    return LoginResponse(id=result.user.id, email=result.user.email, claimed_trip_count=result.claimed_trip_count)


@router.get("/me", response_model=MeResponse)
async def me(current_user: CurrentUser):
    if current_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return MeResponse(id=current_user.id, email=current_user.email)


@router.post("/logout", status_code=204)
async def logout(response: Response, auth: Auth):
    auth.logout(response)
