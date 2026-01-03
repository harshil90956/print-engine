import hashlib
from typing import Union


def sha256_hex(data: Union[bytes, bytearray, memoryview]) -> str:
    h = hashlib.sha256()
    h.update(bytes(data))
    return h.hexdigest()
