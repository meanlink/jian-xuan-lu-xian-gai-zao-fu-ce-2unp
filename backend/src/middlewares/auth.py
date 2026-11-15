from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.config.database import get_db
from src.models import User
from src.utils.security import decode_token
bearer = HTTPBearer(auto_error=False)
def current_user(token: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    try:
        user = db.get(User, decode_token(token.credentials))
        if user is None: raise ValueError('missing user')
        return user
    except Exception:
        raise HTTPException(401, '登录已过期，请重新登录')
def can_write(user=Depends(current_user)):
    if user.role not in ('admin','analyst'): raise HTTPException(403, '观察员只有读取权限')
    return user
def admin_only(user=Depends(current_user)):
    if user.role != 'admin': raise HTTPException(403, '需要业务负责人批准')
    return user
def custodian_only(user=Depends(current_user)):
    # Unblinding authority is personal and non-delegable: even the business owner
    # (admin) cannot unblind, and analysts are rejected before any mapping is read.
    if user.role != 'custodian': raise HTTPException(403, '只有盲态保管员可以登记或揭盲')
    return user
def trial_write(user=Depends(current_user)):
    # Trial creation and blind runs are open to the analysis side and the custodian;
    # observers stay read-only and never get a write entry point.
    if user.role not in ('admin','analyst','custodian'): raise HTTPException(403, '观察员只有读取权限')
    return user
