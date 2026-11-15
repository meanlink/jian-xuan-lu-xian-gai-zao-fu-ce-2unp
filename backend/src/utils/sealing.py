"""盲态映射封存。

封存载荷只包含该批甲/乙与原/新路线的对应。明文只在保管员登记与揭盲计算
时短暂进入服务端内存；数据库仅保存 AES-GCM 密文。密文绑定试验、批次与
批次指纹，任何普通接口、下载、审计和错误信息都不会返回明文。摘要使用
HMAC 而非裸 SHA256，避免外部枚举猜测。
"""
import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.settings import BLIND_SEAL_SECRET

ROUTES = ('原', '新')
ARMS = ('甲', '乙')


def _key():
    return hashlib.sha256(('pick-paired/blind-seal/v1:' + BLIND_SEAL_SECRET).encode()).digest()


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def _aad(trial_id, dataset_id, checksum, rows):
    return f'v1|{trial_id}|{dataset_id}|{checksum}|{rows}'.encode()


def validate_arms(arms):
    """Each batch declares only how 甲/乙 map to the original/new route."""
    if not isinstance(arms, dict) or set(arms) != set(ARMS):
        raise ValueError('映射必须且只能登记甲、乙两个臂')
    if any(arms[x] not in ROUTES for x in ARMS) or arms['甲'] == arms['乙']:
        raise ValueError('甲、乙必须分别对应原路线与新路线')
    return {x: arms[x] for x in ARMS}


def seal(arms, *, trial_id, dataset_id, checksum, rows):
    payload = validate_arms(arms)
    nonce = os.urandom(12)
    blob = AESGCM(_key()).encrypt(nonce, _canonical(payload), _aad(trial_id, dataset_id, checksum, rows))
    return base64.urlsafe_b64encode(nonce + blob).decode()


def open_sealed(sealed, *, trial_id, dataset_id, checksum, rows):
    try:
        raw = base64.urlsafe_b64decode(sealed.encode())
        plain = AESGCM(_key()).decrypt(raw[:12], raw[12:], _aad(trial_id, dataset_id, checksum, rows))
        return validate_arms(json.loads(plain))
    except Exception:
        raise ValueError('封存映射校验失败，请联系盲态保管员核对批次登记')


def digest(arms):
    return hmac.new(_key(), _canonical(validate_arms(arms)), hashlib.sha256).hexdigest()
