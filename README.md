# Universal ROM Dumper v3.0

أداة يونيفرسال لعمل Dump كامل لأي ROM Android وتنظيمها احترافيًا جاهز لبناء Recovery Tree.

## المميزات

- 🌍 **يونيفرسال** — شغالة على أي جهاز Android
- 📥 **تحميل** — Google Drive (gdown), MediaFire, Direct Link
- 🔓 **Auth Gate** — مقفولة بـ Secret Key (الناس العامة تشوف الكود بس مش تشغله)
- 📦 **فك كل حاجة**:
  - boot.img → kernel + dtb + ramdisk
  - vendor_boot.img → vendor ramdisk + vendor dtb
  - recovery.img → recovery ramdisk
  - dtbo.img → individual dtb files
  - vbmeta.img → verification metadata
  - super.img → dynamic partitions (system_a, vendor_a...)
  - system/vendor/product.img → full filesystem extraction
  - payload.bin (OTA) → all partitions
- 🌳 **Recovery Tree Ready** — فولدر جاهز للـ TWRP/OFOX building:
  ```
  recovery_tree_ready/
  ├── kernel
  ├── dtb/
  ├── ramdisk/
  ├── vendor_ramdisk/
  ├── recovery_ramdisk/
  ├── dtbo.img
  ├── vbmeta.img
  ├── device-info.json
  └── BUILD_HINTS.md
  ```
- 📊 **Metadata كامل** — hashes, partition table, device info
- ☁️ **رفع تلقائي** — على GitGud (Gitea) Release + Assets

## الإعداد

### 1. Secrets (ضروري)

| Secret | الوصف |
|--------|-------|
| `GITGUD_TOKEN` | توكين GitGud |
| `DUMPER_AUTH_KEY` | مفتاح الحماية (غيره في `dumper.py` سطر 45) |

### 2. تشغيل الـ Workflow

`Actions → Universal ROM Dumper → Run workflow`

| Input | الوصف |
|-------|-------|
| Source URL | رابط Google Drive / MediaFire / Direct |
| Source type | `gdrive` / `mediafire` / `direct` |
| ROM Format | `auto` / `spflash` / `fastboot` / `ota` / `super_dynamic` |
| Device codename | مثلاً `x6886` أو `gta4l` |
| Device name | الاسم اللطيف (اختياري) |
| GitGud repo | `username/repo` |
| GitGud host | `https://gitgud.io` |

## الـ Auth Gate

الأداة مقفولة. عشان تشغلها:
1. غير `EXPECTED_AUTH` في `dumper.py` (سطر 45)
2. ضيف نفس القيمة في Secret `DUMPER_AUTH_KEY`

الناس العامة تشوف الكود بس لما يشغلوه هيطلعلهم:
```
AUTH FAILED - Dumper locked. Set DUMPER_AUTH_KEY secret.
```

## الـ Output Structure

```
output/
├── partitions_raw/          ← كل الـ .img الخام ( untouched )
├── unpacked/                ← كل حاجة مفكوكة وتنظيفة
│   ├── boot/
│   │   ├── kernel
│   │   ├── dtb/
│   │   ├── ramdisk.cpio
│   │   └── ramdisk_extracted/   ← شجرة كاملة
│   ├── vendor_boot/
│   ├── recovery/
│   ├── dtbo/dtb_files/
│   ├── vbmeta/
│   ├── super/partitions/
│   └── system/extracted/
├── recovery_tree_ready/     ← جاهز للـ TWRP
│   ├── kernel
│   ├── dtb/
│   ├── ramdisk/
│   ├── vendor_ramdisk/
│   ├── recovery_ramdisk/
│   ├── dtbo.img
│   ├── vbmeta.img
│   ├── device-info.json
│   └── BUILD_HINTS.md
└── meta/
    ├── dump_info.json
    ├── partition_table.txt
    └── README.md
```

## ملاحظات

- GitHub Actions: 14GB SSD, 6 hours max
- لو الـ ROM كبير جدًا، استخدم self-hosted runner
- GitGud file limit: غالبًا 100MB per asset. لو أكبر، افتح issue

---
Built with 💀 for the real ones
