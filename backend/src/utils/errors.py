def truncate(text, limit=300):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + '…'


def safe_error(exc):
    """Only surface text authored for users; never leak sealed direction or internals."""
    if isinstance(exc, ValueError):
        return truncate(str(exc))
    from fastapi import HTTPException
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return truncate(exc.detail)
    return '计算失败，请核对批次、方向登记与参数后重试'
