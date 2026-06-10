#!/usr/bin/env python3
"""
Universal ROM Dumper v3.0
Supports ANY Android device - extracts ALL partitions professionally
Ready for TWRP/OrangeFox recovery tree building
"""

import os
import sys
import re
import json
import shutil
import hashlib
import subprocess
import zipfile
import tarfile
import struct
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import requests
from tqdm import tqdm

# ==================== CONFIG ====================
SOURCE_URL = os.environ.get("SOURCE_URL", "")
SOURCE_TYPE = os.environ.get("SOURCE_TYPE", "auto")
ROM_FORMAT = os.environ.get("ROM_FORMAT", "auto")
DEVICE_CODENAME = os.environ.get("DEVICE_CODENAME", "unknown")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "Unknown Device")
GITGUD_TOKEN = os.environ.get("GITGUD_TOKEN", "")
GITGUD_REPO = os.environ.get("GITGUD_REPO", "")
GITGUD_HOST = os.environ.get("GITGUD_HOST", "https://gitgud.io").rstrip("/")
AUTH_KEY = os.environ.get("DUMPER_AUTH_KEY", "")

# DIRS
DOWNLOAD_DIR = Path("downloads")
EXTRACT_DIR = Path("extracted")
OUTPUT_DIR = Path("output")
RAW_DIR = OUTPUT_DIR / "partitions_raw"
UNPACKED_DIR = OUTPUT_DIR / "unpacked"
RECOVERY_TREE_DIR = OUTPUT_DIR / "recovery_tree_ready"
META_DIR = OUTPUT_DIR / "meta"

# Auth key (change this!)
EXPECTED_AUTH = "UNIVERSAL_DUMPER_V3"

# ==================== UTILS ====================
def log(msg, level="INFO"):
    print(f"[DUMPER][{level}] {msg}", flush=True)

def run(cmd, cwd=None, check=True, timeout=300):
    log(f"Running: {cmd}")
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True,
        timeout=timeout
    )
    if check and result.returncode != 0:
        log(f"WARN: {result.stderr[:200]}", "WARN")
    return result

def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def file_size(path):
    return path.stat().st_size

def size_human(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def is_sparse(path):
    with open(path, "rb") as f:
        magic = f.read(4)
    return magic == b"\\x3a\\xff\\x26\\xed"

def sparse_to_raw(sparse_path, raw_path):
    run(f'simg2img "{sparse_path}" "{raw_path}"')

def detect_file_type(path):
    with open(path, "rb") as f:
        header = f.read(16)

    if header[:4] == b"\\x3a\\xff\\x26\\xed":
        return "sparse_img"
    if header[:4] == b"\\x53\\x45\\x46\\x42":
        return "super"
    if header[:4] == b"PK\\x03\\x04":
        return "zip"
    if header[:6] == b"7z\\xbc\\xaf\\x27\\x1c":
        return "7z"
    if header[:4] == b"ustar":
        return "tar"
    if header[:2] == b"\\x1f\\x8b":
        return "gzip"
    if header[:2] == b"\\xfd\\x37":
        return "xz"
    if header[:4] == b"\\x28\\xb5\\x2f\\xfd":
        return "zstd"
    if b"ANDROID!" in header:
        return "boot_img"
    if header[:4] == b"\\xd0\\x0d\\x24\\x40":
        return "dtbo"
    if header[:4] == b"\\x41\\x56\\x42\\x30":
        return "vbmeta"
    if header[:4] == b"\\x67\\x44\\x6c\\x61":
        return "lz4"
    return "unknown"

# ==================== AUTH ====================
def auth_check():
    if AUTH_KEY != EXPECTED_AUTH:
        log("AUTH FAILED - Dumper locked. Set DUMPER_AUTH_KEY secret.", "ERROR")
        sys.exit(1)
    log("Auth verified. Dumper unlocked.", "OK")

# ==================== DOWNLOADERS ====================
def download_gdrive(url):
    import gdown
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    file_id = None
    for pat in [r"id=([\\w-]+)", r"/d/([\\w-]+)", r"file/d/([\\w-]+)"]:
        m = re.search(pat, url)
        if m:
            file_id = m.group(1)
            break

    if not file_id:
        raise ValueError("Cannot extract GDrive file ID")

    out = DOWNLOAD_DIR / "rom_file"
    gdown.download(id=file_id, output=str(out), quiet=False, fuzzy=True)
    return out

def download_mediafire(url):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = DOWNLOAD_DIR / "rom_file"

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64)"
    r = session.get(url, allow_redirects=True, timeout=60)

    direct = re.search(r'"(https://download\\d+\\.mediafire\\.com/[^"]+)"', r.text)
    dl_url = direct.group(1) if direct else r.url

    r2 = session.get(dl_url, stream=True, timeout=300)
    total = int(r2.headers.get("content-length", 0))

    with open(out, "wb") as f, tqdm(total=total, unit="B", unit_scale=True) as bar:
        for chunk in r2.iter_content(8192):
            if chunk:
                f.write(chunk)
                bar.update(len(chunk))
    return out

def download_direct(url):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = DOWNLOAD_DIR / "rom_file"
    run(f'wget --progress=bar:force -O "{out}" "{url}"', timeout=600)
    return out

def download(url):
    if "drive.google.com" in url or SOURCE_TYPE == "gdrive":
        return download_gdrive(url)
    elif "mediafire.com" in url or SOURCE_TYPE == "mediafire":
        return download_mediafire(url)
    else:
        return download_direct(url)

# ==================== EXTRACTION ====================
def extract_archive(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    path = Path(archive)

    if zipfile.is_zipfile(path):
        log("Extracting ZIP archive...")
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(dest)
    elif tarfile.is_tarfile(path):
        log("Extracting TAR archive...")
        with tarfile.open(path, "r:*") as t:
            t.extractall(dest)
    elif path.suffix == ".7z":
        log("Extracting 7Z archive...")
        run(f'7z x "{path}" -o"{dest}" -y')
    else:
        shutil.copy2(path, dest / path.name)

# ==================== BOOT/RECOVERY UNPACK ====================
def unpack_bootimg(img_path, out_dir):
    """Unpack boot/vendor_boot/recovery using AIK + magiskboot"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Unpacking {img_path.name}...")

    # Try AIK first
    aik = Path("/tmp/AIK")
    if aik.exists():
        run(f'cp "{img_path}" "{out_dir}/{img_path.name}"')
        result = run(
            f'cd "{out_dir}" && bash {aik}/unpackimg.sh "{img_path.name}"',
            check=False, timeout=120
        )
        if result.returncode == 0:
            log(f"AIK unpacked {img_path.name} successfully")
            return True

    # Fallback: magiskboot
    magiskboot = shutil.which("magiskboot")
    if magiskboot:
        run(f'"{magiskboot}" unpack "{img_path}" -out "{out_dir}"', check=False, timeout=60)
        if any((out_dir / f).exists() for f in ["kernel", "ramdisk.cpio", "dtb"]):
            log(f"magiskboot unpacked {img_path.name}")
            return True

    return False

def extract_ramdisk(ramdisk_path, out_dir):
    """Extract cpio ramdisk"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not ramdisk_path.exists():
        return

    # Try to decompress first
    decomp = ramdisk_path
    for ext, cmd in [(".gz", "gzip -d"), (".lz4", "lz4 -d"), (".xz", "xz -d")]:
        if ramdisk_path.suffix == ext or ramdisk_path.name.endswith(ext):
            run(f'{cmd} "{ramdisk_path}" -c > "{out_dir}/ramdisk.cpio"', check=False)
            decomp = out_dir / "ramdisk.cpio"
            break

    if decomp.exists():
        run(f'cd "{out_dir}" && cpio -idmv < "{decomp}" 2>/dev/null || true', check=False, timeout=60)
        # Also try with magiskboot
        magiskboot = shutil.which("magiskboot")
        if magiskboot:
            run(f'"{magiskboot}" cpio "{decomp}" extract 2>/dev/null || true', cwd=out_dir, check=False)

def unpack_dtbo(dtbo_path, out_dir):
    """Extract dtbo.img into individual dtb files"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Unpacking dtbo: {dtbo_path.name}")

    # Try mkdtboimg.py
    dtbo_tool = Path("/tmp/mkdtboimg.py")
    if not dtbo_tool.exists():
        run("wget -q https://raw.githubusercontent.com/LineageOS/android_system_tools_dtbtool/main/mkdtboimg.py -O /tmp/mkdtboimg.py", check=False)

    result = run(f'python3 /tmp/mkdtboimg.py dump "{dtbo_path}" --output "{out_dir}"', check=False, timeout=60)

    # Also try manual extraction
    if result.returncode != 0:
        run(f'python3 -c "
import struct
with open(\\"{dtbo_path}\\", \\"rb\\") as f:
    data = f.read()
    magic = struct.unpack(\\"<I\\", data[:4])[0]
    if magic == 0xd7b7ab1e:
        print(\\"Valid dtbo header found\\")
"', check=False)

def unpack_vbmeta(vbmeta_path, out_dir):
    """Extract vbmeta info"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Reading vbmeta: {vbmeta_path.name}")

    # Copy vbmeta
    shutil.copy2(vbmeta_path, out_dir / vbmeta_path.name)

    # Try avbtool info
    avbtool = Path("/tmp/avbtool/avbtool.py")
    if avbtool.exists():
        result = run(f'python3 "{avbtool}" info_image --image "{vbmeta_path}" > "{out_dir}/vbmeta_info.txt"', check=False)

    # Manual header parse
    with open(vbmeta_path, "rb") as f:
        header = f.read(256)
    info = {
        "magic": header[:4].hex(),
        "version_major": struct.unpack("<I", header[4:8])[0] if len(header) > 8 else 0,
        "size": file_size(vbmeta_path),
    }
    with open(out_dir / "vbmeta_header.json", "w") as f:
        json.dump(info, f, indent=2)

# ==================== SUPER / DYNAMIC PARTITIONS ====================
def unpack_super(super_path, out_dir):
    """Unpack super.img using lpunpack"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Unpacking super: {super_path.name} ({size_human(file_size(super_path))})")

    lpunpack = shutil.which("lpunpack")
    if lpunpack:
        run(f'"{lpunpack}" "{super_path}" "{out_dir}"', timeout=300)
    else:
        log("lpunpack not found, trying manual extraction", "WARN")

    # List extracted partitions
    extracted = []
    for f in out_dir.iterdir():
        if f.is_file() and f.suffix == ".img":
            extracted.append(f.name)
            # If sparse, convert
            if is_sparse(f):
                raw = out_dir / f"{f.stem}_raw.img"
                sparse_to_raw(f, raw)

    log(f"Super unpacked: {len(extracted)} partitions")
    return extracted

# ==================== EXT4 EXTRACT ====================
def extract_ext4(img_path, out_dir):
    """Mount and extract ext4 image contents"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Extracting ext4: {img_path.name}")

    mount_point = out_dir / "_mounted"
    mount_point.mkdir(exist_ok=True)

    # Try fuse2fs (userspace mount)
    run(f'fuse2fs -o ro "{img_path}" "{mount_point}" 2>/dev/null || true', check=False, timeout=30)

    # If mounted, copy contents
    if list(mount_point.iterdir()):
        run(f'cp -r "{mount_point}/." "{out_dir}/" 2>/dev/null || true', check=False)
        run(f'fusermount -u "{mount_point}" 2>/dev/null || true', check=False)
        return True

    # Fallback: debugfs
    run(f'debugfs -R "ls -p /" "{img_path}" > "{out_dir}/file_list.txt" 2>/dev/null || true', check=False)

    return False

# ==================== MAIN PROCESSING ====================
def process_all_partitions(extracted_dir):
    """Process ALL found partitions comprehensively"""

    all_imgs = list(extracted_dir.rglob("*.img"))
    all_bins = list(extracted_dir.rglob("*.bin"))
    all_files = all_imgs + all_bins

    log(f"Found {len(all_files)} partition files")

    partition_info = {}

    for f in sorted(all_files):
        fname = f.name
        ftype = detect_file_type(f)
        log(f"Processing: {fname} (type: {ftype})")

        # Copy raw to partitions_raw
        raw_dest = RAW_DIR / fname
        shutil.copy2(f, raw_dest)

        # Create unpack directory
        unpack_base = UNPACKED_DIR / f.stem
        unpack_base.mkdir(parents=True, exist_ok=True)

        info = {
            "original_name": fname,
            "size": file_size(f),
            "size_human": size_human(file_size(f)),
            "detected_type": ftype,
            "md5": md5(f),
            "sha256": sha256(f),
            "unpacked": False,
        }

        # Handle sparse images first
        if ftype == "sparse_img":
            raw_img = unpack_base / f"{f.stem}_raw.img"
            sparse_to_raw(f, raw_img)
            info["raw_img"] = str(raw_img.relative_to(OUTPUT_DIR))
            # Re-detect on raw
            ftype = detect_file_type(raw_img)
            f = raw_img  # Work on raw from now

        # Boot / Vendor_Boot / Recovery images
        if ftype == "boot_img" or fname in ["boot.img", "vendor_boot.img", "recovery.img", "init_boot.img"]:
            unpacked_ok = unpack_bootimg(f, unpack_base)
            info["unpacked"] = unpacked_ok

            if unpacked_ok:
                # Extract ramdisk if exists
                for ramdisk_name in ["ramdisk.cpio", "ramdisk.cpio.gz", "ramdisk"]:
                    ramdisk = unpack_base / ramdisk_name
                    if ramdisk.exists():
                        ramdisk_extracted = unpack_base / "ramdisk_extracted"
                        extract_ramdisk(ramdisk, ramdisk_extracted)
                        info["ramdisk_extracted"] = str(ramdisk_extracted.relative_to(OUTPUT_DIR))
                        break

                # Note DTB presence
                dtb_files = list(unpack_base.glob("*.dtb")) + list(unpack_base.glob("dtb"))
                if dtb_files:
                    info["dtb_files"] = [str(d.relative_to(OUTPUT_DIR)) for d in dtb_files]

        # DTBO
        elif ftype == "dtbo" or fname == "dtbo.img":
            dtbo_out = unpack_base / "dtb_files"
            unpack_dtbo(f, dtbo_out)
            info["unpacked"] = dtbo_out.exists() and any(dtbo_out.iterdir())

        # VBMETA
        elif ftype == "vbmeta" or fname in ["vbmeta.img", "vbmeta_system.img", "vbmeta_vendor.img"]:
            vbmeta_out = unpack_base / "info"
            unpack_vbmeta(f, vbmeta_out)
            info["unpacked"] = True

        # SUPER (dynamic partitions)
        elif ftype == "super" or fname == "super.img":
            super_out = unpack_base / "partitions"
            extracted = unpack_super(f, super_out)
            info["unpacked"] = len(extracted) > 0
            info["contained_partitions"] = extracted

            # Process each extracted partition
            for sub in super_out.iterdir():
                if sub.is_file() and sub.suffix == ".img":
                    sub_unpack = UNPACKED_DIR / sub.stem
                    sub_unpack.mkdir(exist_ok=True)
                    sub_type = detect_file_type(sub)
                    if sub_type == "boot_img":
                        unpack_bootimg(sub, sub_unpack)
                    elif "ext" in str(sub_type) or sub_type == "unknown":
                        extract_ext4(sub, sub_unpack / "extracted")

        # EXT4 system/vendor/product images
        elif fname in ["system.img", "vendor.img", "product.img", "system_ext.img", "odm.img", "vendor_dlkm.img"]:
            ext_out = unpack_base / "extracted"
            extracted_ok = extract_ext4(f, ext_out)
            info["unpacked"] = extracted_ok

        # Generic .img - try everything
        elif f.suffix == ".img":
            # Try boot unpack
            boot_ok = unpack_bootimg(f, unpack_base / "boot_attempt")
            if boot_ok:
                info["unpacked"] = True
                info["format"] = "boot_image"
            else:
                # Try ext4
                ext_ok = extract_ext4(f, unpack_base / "ext4_attempt")
                if ext_ok:
                    info["unpacked"] = True
                    info["format"] = "ext4"

        partition_info[fname] = info

    return partition_info

# ==================== RECOVERY TREE BUILDER ====================
def build_recovery_tree(partition_info):
    """Build a ready-to-use recovery tree structure"""
    log("Building recovery tree...")

    RECOVERY_TREE_DIR.mkdir(parents=True, exist_ok=True)

    tree = {
        "kernel": None,
        "dtb": [],
        "dtbo": None,
        "ramdisk": None,
        "recovery_ramdisk": None,
        "vendor_ramdisk": None,
        "vbmeta": None,
        "boot_img": None,
        "vendor_boot_img": None,
        "recovery_img": None,
    }

    # Find boot.img
    boot_raw = RAW_DIR / "boot.img"
    if boot_raw.exists():
        tree["boot_img"] = str(boot_raw.relative_to(OUTPUT_DIR))
        boot_unpacked = UNPACKED_DIR / "boot"
        if boot_unpacked.exists():
            # Kernel
            for kname in ["kernel", "kernel.gz", "Image", "Image.gz", "zImage"]:
                k = boot_unpacked / kname
                if k.exists():
                    tree["kernel"] = str(k.relative_to(OUTPUT_DIR))
                    # Symlink to recovery_tree
                    dest = RECOVERY_TREE_DIR / "kernel"
                    if dest.exists() or dest.is_symlink():
                        dest.unlink()
                    os.symlink(os.path.abspath(k), dest)
                    break

            # DTB
            dtb_dir = RECOVERY_TREE_DIR / "dtb"
            dtb_dir.mkdir(exist_ok=True)
            for dtb in boot_unpacked.rglob("*.dtb"):
                tree["dtb"].append(str(dtb.relative_to(OUTPUT_DIR)))
                shutil.copy2(dtb, dtb_dir / dtb.name)
            if (boot_unpacked / "dtb").exists():
                shutil.copy2(boot_unpacked / "dtb", dtb_dir / "dtb")

            # Ramdisk
            ramdisk_extracted = boot_unpacked / "ramdisk_extracted"
            if ramdisk_extracted.exists():
                tree["ramdisk"] = str(ramdisk_extracted.relative_to(OUTPUT_DIR))
                dest = RECOVERY_TREE_DIR / "ramdisk"
                if dest.exists() or dest.is_symlink():
                    shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
                shutil.copytree(ramdisk_extracted, dest, symlinks=True)

    # Find vendor_boot.img
    vendor_boot_raw = RAW_DIR / "vendor_boot.img"
    if vendor_boot_raw.exists():
        tree["vendor_boot_img"] = str(vendor_boot_raw.relative_to(OUTPUT_DIR))
        vb_unpacked = UNPACKED_DIR / "vendor_boot"
        if vb_unpacked.exists():
            vb_ramdisk = vb_unpacked / "ramdisk_extracted"
            if vb_ramdisk.exists():
                tree["vendor_ramdisk"] = str(vb_ramdisk.relative_to(OUTPUT_DIR))
                dest = RECOVERY_TREE_DIR / "vendor_ramdisk"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(vb_ramdisk, dest, symlinks=True)

            # Vendor DTB
            for dtb in vb_unpacked.rglob("*.dtb"):
                if dtb not in [Path(d) for d in tree["dtb"]]:
                    tree["dtb"].append(str(dtb.relative_to(OUTPUT_DIR)))
                    shutil.copy2(dtb, RECOVERY_TREE_DIR / "dtb" / f"vendor_{dtb.name}")

    # Find recovery.img
    recovery_raw = RAW_DIR / "recovery.img"
    if recovery_raw.exists():
        tree["recovery_img"] = str(recovery_raw.relative_to(OUTPUT_DIR))
        rec_unpacked = UNPACKED_DIR / "recovery"
        if rec_unpacked.exists():
            rec_ramdisk = rec_unpacked / "ramdisk_extracted"
            if rec_ramdisk.exists():
                tree["recovery_ramdisk"] = str(rec_ramdisk.relative_to(OUTPUT_DIR))
                dest = RECOVERY_TREE_DIR / "recovery_ramdisk"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(rec_ramdisk, dest, symlinks=True)

    # Find dtbo.img
    dtbo_raw = RAW_DIR / "dtbo.img"
    if dtbo_raw.exists():
        tree["dtbo"] = str(dtbo_raw.relative_to(OUTPUT_DIR))
        shutil.copy2(dtbo_raw, RECOVERY_TREE_DIR / "dtbo.img")

    # Find vbmeta
    vbmeta_raw = RAW_DIR / "vbmeta.img"
    if vbmeta_raw.exists():
        tree["vbmeta"] = str(vbmeta_raw.relative_to(OUTPUT_DIR))
        shutil.copy2(vbmeta_raw, RECOVERY_TREE_DIR / "vbmeta.img")

    # Generate device-info.json for recovery building
    device_info = {
        "device": {
            "codename": DEVICE_CODENAME,
            "name": DEVICE_NAME,
        },
        "recovery_tree": {
            "kernel": tree["kernel"],
            "dtb_count": len(tree["dtb"]),
            "has_vendor_boot": tree["vendor_boot_img"] is not None,
            "has_dtbo": tree["dtbo"] is not None,
        },
        "paths": tree,
    }

    with open(RECOVERY_TREE_DIR / "device-info.json", "w") as f:
        json.dump(device_info, f, indent=2)

    # Generate Android.bp / Android.mk hints
    with open(RECOVERY_TREE_DIR / "BUILD_HINTS.md", "w") as f:
        f.write(f"""# Recovery Tree Build Hints for {DEVICE_CODENAME}

## Device Info
- Codename: `{DEVICE_CODENAME}`
- Kernel: `{'Yes' if tree['kernel'] else 'No'}`
- DTB files: {len(tree['dtb'])}
- Vendor Boot: `{'Yes' if tree['vendor_boot_img'] else 'No'}`

## TWRP Build Steps
```bash
# 1. Source build environment
source build/envsetup.sh

# 2. Lunch device
lunch twrp_{DEVICE_CODENAME}-eng

# 3. Build
mka recoveryimage
```

## Key Files
| File | Location |
|------|----------|
| Kernel | `kernel` |
| DTB | `dtb/` |
| Ramdisk | `ramdisk/` |
| Vendor Ramdisk | `vendor_ramdisk/` (if vendor_boot) |
| Recovery Ramdisk | `recovery_ramdisk/` |
| DTBO | `dtbo.img` |
| VBMETA | `vbmeta.img` |

## Notes
- Check `device-info.json` for full paths
- All raw .img files are in `../partitions_raw/`
""")

    log(f"Recovery tree built at: {RECOVERY_TREE_DIR}")
    log(f"  - Kernel: {'YES' if tree['kernel'] else 'NO'}")
    log(f"  - DTB files: {len(tree['dtb'])}")
    log(f"  - Vendor boot: {'YES' if tree['vendor_boot_img'] else 'NO'}")
    log(f"  - Recovery img: {'YES' if tree['recovery_img'] else 'NO'}")

    return tree

# ==================== METADATA ====================
def generate_full_metadata(partition_info, recovery_tree):
    META_DIR.mkdir(parents=True, exist_ok=True)

    # Full dump info
    dump_info = {
        "device": {
            "codename": DEVICE_CODENAME,
            "name": DEVICE_NAME,
        },
        "dump": {
            "date": datetime.utcnow().isoformat() + "Z",
            "dumper_version": "3.0-universal",
            "source_url": SOURCE_URL,
        },
        "partitions": partition_info,
        "recovery_tree": recovery_tree,
        "statistics": {
            "total_partitions": len(partition_info),
            "total_raw_size": sum(p["size"] for p in partition_info.values()),
            "unpacked_partitions": sum(1 for p in partition_info.values() if p.get("unpacked")),
        },
    }

    with open(META_DIR / "dump_info.json", "w") as f:
        json.dump(dump_info, f, indent=2)

    # Generate partition table
    with open(META_DIR / "partition_table.txt", "w") as f:
        f.write(f"# Partition Table for {DEVICE_CODENAME}\\n")
        f.write(f"# Generated: {datetime.utcnow().isoformat()}Z\\n\\n")
        f.write(f"{'Partition':<30} {'Size':<15} {'Type':<20} {'MD5 (first 16)':<20}\\n")
        f.write("-" * 85 + "\\n")
        for name, info in sorted(partition_info.items()):
            f.write(f"{name:<30} {info['size_human']:<15} {info['detected_type']:<20} {info['md5'][:16]:<20}\\n")

    # Generate README
    readme = f"""# {DEVICE_NAME} ({DEVICE_CODENAME}) - Full ROM Dump

**Dump Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC  
**Dumper:** Universal ROM Dumper v3.0  

## Structure

```
output/
├── partitions_raw/          ← All raw .img/.bin files (untouched)
├── unpacked/                ← Each partition fully unpacked
│   ├── boot/
│   │   ├── kernel
│   │   ├── dtb/
│   │   ├── ramdisk.cpio
│   │   └── ramdisk_extracted/   ← Full ramdisk tree
│   ├── vendor_boot/
│   ├── recovery/
│   ├── dtbo/
│   │   └── dtb_files/           ← Individual DTB files
│   ├── vbmeta/
│   ├── super/
│   │   └── partitions/          ← Dynamic partitions
│   └── system/
│       └── extracted/           ← Full file system
├── recovery_tree_ready/     ← Ready for TWRP/OFOX building
│   ├── kernel
│   ├── dtb/
│   ├── ramdisk/
│   ├── vendor_ramdisk/      (if vendor_boot exists)
│   ├── recovery_ramdisk/
│   ├── dtbo.img
│   ├── vbmeta.img
│   ├── device-info.json
│   └── BUILD_HINTS.md
└── meta/
    ├── dump_info.json       ← Full metadata + hashes
    ├── partition_table.txt
    └── README.md
```

## Statistics
- **Total Partitions:** {len(partition_info)}
- **Unpacked:** {sum(1 for p in partition_info.values() if p.get('unpacked'))}
- **Total Size:** {size_human(sum(p['size'] for p in partition_info.values()))}

## Recovery Tree
- **Kernel:** {'✅' if recovery_tree.get('kernel') else '❌'}
- **DTB Files:** {len(recovery_tree.get('dtb', []))}
- **Vendor Boot:** {'✅' if recovery_tree.get('vendor_boot_img') else '❌'}
- **DTBO:** {'✅' if recovery_tree.get('dtbo') else '❌'}
- **VBMETA:** {'✅' if recovery_tree.get('vbmeta') else '❌'}

## Hashes
See `meta/dump_info.json` for MD5 and SHA256 of every file.

---
Dumped with ❤️ using Universal ROM Dumper
"""

    with open(OUTPUT_DIR / "README.md", "w") as f:
        f.write(readme)

    with open(META_DIR / "README.md", "w") as f:
        f.write(readme)

    log("Metadata generated")

# ==================== GITGUD UPLOAD ====================
def upload_to_gitgud():
    if not GITGUD_TOKEN or not GITGUD_REPO:
        log("No GitGud credentials. Skipping upload.", "WARN")
        return None

    owner, repo = GITGUD_REPO.split("/")
    tag = f"{DEVICE_CODENAME}-dump-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    headers = {
        "Authorization": f"token {GITGUD_TOKEN}",
        "Content-Type": "application/json",
    }

    # Create release
    release_url = f"{GITGUD_HOST}/api/v1/repos/{owner}/{repo}/releases"
    release_data = {
        "tag_name": tag,
        "name": f"{DEVICE_NAME} ({DEVICE_CODENAME}) Full Dump",
        "body": f"Complete ROM dump for {DEVICE_NAME}\\n\\nGenerated by Universal ROM Dumper v3.0",
        "target_commitish": "master",
    }

    r = requests.post(release_url, headers=headers, json=release_data, timeout=60)
    if r.status_code not in [200, 201]:
        log(f"Failed to create release: {r.status_code}", "ERROR")
        return None

    release = r.json()
    release_id = release["id"]
    upload_base = f"{GITGUD_HOST}/api/v1/repos/{owner}/{repo}/releases/{release_id}/assets"

    # Upload files (prioritize important ones)
    upload_queue = []

    # 1. Recovery tree files (most important)
    for f in sorted(RECOVERY_TREE_DIR.rglob("*")):
        if f.is_file():
            upload_queue.append((f, f"recovery_tree/{f.relative_to(RECOVERY_TREE_DIR)}"))

    # 2. Raw partitions
    for f in sorted(RAW_DIR.iterdir()):
        if f.is_file():
            upload_queue.append((f, f"partitions_raw/{f.name}"))

    # 3. Metadata
    for f in [META_DIR / "dump_info.json", META_DIR / "partition_table.txt", OUTPUT_DIR / "README.md"]:
        if f.exists():
            upload_queue.append((f, f"meta/{f.name}"))

    uploaded = []
    for file_path, asset_name in upload_queue:
        log(f"Uploading {asset_name} ({size_human(file_size(file_path))})...")
        try:
            with open(file_path, "rb") as fd:
                files = {"attachment": (asset_name, fd)}
                r = requests.post(
                    f"{upload_base}?name={asset_name}",
                    headers={"Authorization": f"token {GITGUD_TOKEN}"},
                    files=files,
                    timeout=300,
                )
            if r.status_code in [200, 201]:
                asset = r.json()
                uploaded.append({
                    "name": asset_name,
                    "url": asset["browser_download_url"],
                    "size": asset["size"],
                })
                log(f"✅ {asset_name}")
            else:
                log(f"❌ {asset_name} - {r.status_code}", "WARN")
        except Exception as e:
            log(f"❌ {asset_name} - {e}", "WARN")

    # Print results
    log("\\n" + "="*70)
    log("🎯 DIRECT DOWNLOAD LINKS")
    log("="*70)
    for u in uploaded[:20]:  # Show first 20
        log(f"{u['name']}: {u['url']}")
    if len(uploaded) > 20:
        log(f"... and {len(uploaded) - 20} more files")
    log(f"\\n📦 Release: {release['html_url']}")
    log("="*70)

    # Save URL
    with open(OUTPUT_DIR / "RELEASE_URL.txt", "w") as f:
        f.write(release["html_url"] + "\\n")
        for u in uploaded:
            f.write(f"{u['name']}: {u['url']}\\n")

    return release["html_url"]

# ==================== MAIN ====================
def main():
    log("="*70)
    log("UNIVERSAL ROM DUMPER v3.0")
    log(f"Device: {DEVICE_NAME} ({DEVICE_CODENAME})")
    log("="*70)

    auth_check()

    # Clean
    for d in [DOWNLOAD_DIR, EXTRACT_DIR, OUTPUT_DIR]:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    UNPACKED_DIR.mkdir(parents=True, exist_ok=True)
    RECOVERY_TREE_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Download
    log(f"Source: {SOURCE_URL}")
    downloaded = download(SOURCE_URL)
    log(f"Downloaded: {downloaded.name} ({size_human(file_size(downloaded))})")

    # Step 2: Extract archive if needed
    fmt = ROM_FORMAT if ROM_FORMAT != "auto" else "auto"
    ftype = detect_file_type(downloaded)

    if ftype in ["zip", "7z", "tar", "gzip", "xz", "zstd"]:
        extract_archive(downloaded, EXTRACT_DIR)
    else:
        shutil.copy2(downloaded, EXTRACT_DIR / downloaded.name)

    # Step 3: Process ALL partitions
    partition_info = process_all_partitions(EXTRACT_DIR)

    # Step 4: Build recovery tree
    recovery_tree = build_recovery_tree(partition_info)

    # Step 5: Generate metadata
    generate_full_metadata(partition_info, recovery_tree)

    # Step 6: Upload to GitGud
    release_url = upload_to_gitgud()

    # Summary
    total_size = sum(p["size"] for p in partition_info.values())
    unpacked_count = sum(1 for p in partition_info.values() if p.get("unpacked"))

    log("\\n" + "="*70)
    log("📊 DUMP SUMMARY")
    log("="*70)
    log(f"Device: {DEVICE_NAME} ({DEVICE_CODENAME})")
    log(f"Total partitions: {len(partition_info)}")
    log(f"Unpacked: {unpacked_count}")
    log(f"Total size: {size_human(total_size)}")
    log(f"Recovery tree: {RECOVERY_TREE_DIR}")
    log(f"Raw partitions: {RAW_DIR}")
    if release_url:
        log(f"Release: {release_url}")
    log("="*70)
    log("✅ DONE!")

if __name__ == "__main__":
    main()
