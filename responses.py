import time


def api_response(code: int = 200, msg: str = "操作成功", data=None):
    return {
        "code": code,
        "msg": msg,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
