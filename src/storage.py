"""ที่เก็บไฟล์: เครื่อง (path ปกติ) หรือ S3 (s3://bucket/...) ผ่าน API แบบ pathlib เดียวกัน

    storage.configure(config.get("storage"))   # ครั้งเดียวตอนเริ่มโปรแกรม (profile / region)
    p = storage.path("data/raw/cops", root)    # -> pathlib.Path
    p = storage.path("s3://b/pd/data/raw/cops")  # -> UPath (มี / glob rglob exists open name stem suffix)

layout บน S3 = layout ในเครื่องแบบ 1:1 นับจาก root ของโปรเจค:
    data/raw/cops/COPS-8/...  <->  <s3_root>/data/raw/cops/COPS-8/...
"""
import hashlib
from pathlib import Path

_OPTIONS = {}


def configure(cfg):
    """อ่านค่า storage จาก config: aws_profile, region (ไม่ใส่ = ใช้ค่า default ของ AWS CLI / env)"""
    _OPTIONS.clear()
    cfg = cfg or {}
    if cfg.get("aws_profile"):
        _OPTIONS["profile"] = cfg["aws_profile"]
    if cfg.get("region"):
        _OPTIONS["client_kwargs"] = {"region_name": cfg["region"]}
    if cfg.get("endpoint_url"):                       # ใช้กับ S3 จำลอง (ทดสอบ) หรือ S3-compatible
        _OPTIONS.setdefault("client_kwargs", {})["endpoint_url"] = cfg["endpoint_url"]


def is_remote(p):
    s = str(p)
    return "://" in s and not s.startswith("file://")


def path(p, root=None):
    """local -> Path (ต่อกับ root ถ้าเป็น path สัมพัทธ์), remote -> UPath"""
    if is_remote(p):
        from upath import UPath
        opts = _OPTIONS if str(p).startswith("s3://") else {}
        return UPath(str(p), **opts)
    p = Path(p)
    return p if p.is_absolute() or root is None else Path(root) / p


def fs_of(p):
    """fsspec filesystem ของ remote path"""
    import fsspec
    protocol = str(p).split("://", 1)[0]
    opts = _OPTIONS if protocol == "s3" else {}
    return fsspec.filesystem(protocol, **opts)


def mirror(local, root, s3_root):
    """path ในเครื่อง (ใต้ root ของโปรเจค) -> ตำแหน่งเดียวกันบน S3"""
    rel = Path(local).resolve().relative_to(Path(root).resolve()).as_posix()
    return f"{str(s3_root).rstrip('/')}/{rel}"


def s3_root(cfg):
    root = ((cfg or {}).get("storage") or {}).get("s3_root")
    if not root or "CHANGE-ME" in root:
        raise SystemExit("ยังไม่ได้ตั้ง storage.s3_root ใน config/config.yaml (เช่น s3://my-bucket/pd-pipeline)")
    return root


def md5_file(p, chunk=8 * 1024 * 1024):
    h = hashlib.md5()
    with path(p).open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def remote_md5(fs, key):
    """md5 ที่เก็บไว้ใน metadata ของ object ตอนอัปโหลด (None ถ้าไม่มี หรือไม่มีไฟล์)"""
    try:
        meta = fs.metadata(key) if hasattr(fs, "metadata") else {}
    except FileNotFoundError:
        return None
    return {k.lower(): v for k, v in (meta or {}).items()}.get("md5")


def upload(local, remote, overwrite=False):
    """อัปโหลด 1 ไฟล์พร้อมเก็บ md5 ใน metadata แล้วตรวจขนาดซ้ำ
    คืน 'skip' ถ้าบน S3 มีไฟล์ md5 เดียวกันแล้ว, 'uploaded' ถ้าอัปโหลด"""
    local = Path(local)
    fs = fs_of(remote)
    key = str(remote).split("://", 1)[1]
    md5 = md5_file(local)
    if not overwrite and remote_md5(fs, key) == md5:
        return "skip"
    kwargs = {"Metadata": {"md5": md5}} if str(remote).startswith("s3://") else {}
    fs.put_file(str(local), key, **kwargs)
    size = fs.info(key)["size"]
    if size != local.stat().st_size:
        raise IOError(f"{remote}: ขนาดบน S3 ({size}) ไม่เท่ากับในเครื่อง ({local.stat().st_size})")
    return "uploaded"


def download(remote, local, overwrite=False):
    """ดาวน์โหลด 1 ไฟล์แล้วตรวจ md5 กับ metadata (ถ้ามี)
    คืน 'skip' ถ้าในเครื่องมีไฟล์ md5 ตรงกันแล้ว, 'downloaded' ถ้าโหลด"""
    local = Path(local)
    fs = fs_of(remote)
    key = str(remote).split("://", 1)[1]
    want = remote_md5(fs, key)
    if not overwrite and local.exists() and want and md5_file(local) == want:
        return "skip"
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".part")
    fs.get_file(key, str(tmp))
    if want and md5_file(tmp) != want:
        tmp.unlink()
        raise IOError(f"{remote}: md5 ของไฟล์ที่โหลดไม่ตรงกับ metadata")
    tmp.replace(local)
    return "downloaded"
