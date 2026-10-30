#!/usr/bin/env python3
"""Upload the static site to an Alibaba Cloud OSS bucket."""
import mimetypes
import os
from pathlib import Path

try:
    import oss2
except ImportError as exc:
    raise SystemExit("Install the OSS SDK first: python -m pip install oss2") from exc

ROOT = Path(__file__).resolve().parents[1]
values = {key: os.environ.get(key, "").strip() for key in ("OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")}
missing = [key for key, value in values.items() if not value]
if missing:
    raise SystemExit("Missing required environment variables: " + ", ".join(missing))

bucket = oss2.Bucket(oss2.Auth(values["OSS_ACCESS_KEY_ID"], values["OSS_ACCESS_KEY_SECRET"]), values["OSS_ENDPOINT"], values["OSS_BUCKET"])
for path in (ROOT / "index.html", ROOT / "data" / "papers.json"):
    key = path.relative_to(ROOT).as_posix()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    bucket.put_object_from_file(key, str(path), headers={"Content-Type": content_type, "Cache-Control": "no-cache"})
    print(f"Uploaded {key}")
config_path = ROOT / "data" / "site-config.json"
if config_path.exists():
    bucket.put_object_from_file("data/site-config.json", str(config_path), headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    print("Uploaded data/site-config.json")
print("Upload complete. Verify the custom domain, HTTPS certificate, ICP record, and mainland access separately.")
