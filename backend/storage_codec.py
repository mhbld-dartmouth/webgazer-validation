"""Read compressed study records consistently across S3 and Google Cloud Storage.

Older GCS objects were labelled Content-Encoding:gzip. GCS can transparently
decompress them before boto3 receives the body. New writes are application/gzip
without Content-Encoding so they remain compressed in transit.
"""
import gzip
import json


def decode_json_bytes(payload: bytes):
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    return json.loads(payload)


def ensure_gzip_bytes(payload: bytes) -> bytes:
    # Raw archives always contain actual gzip, even if GCS transcoded the response.
    return payload if payload[:2] == b"\x1f\x8b" else gzip.compress(payload, mtime=0)


def read_object_json(client, bucket: str, key: str):
    return decode_json_bytes(client.get_object(Bucket=bucket, Key=key)["Body"].read())
