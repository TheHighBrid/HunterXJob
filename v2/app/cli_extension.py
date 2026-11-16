"""CLI commands for the HunterXJob portable extension."""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path


def _sync_extension_data(db_path: Path | None = None) -> None:
    from app.db import SessionLocal
    from app.models import AnswerPolicy
    from app.profile import verified_profile

    db = SessionLocal()
    try:
        vp = verified_profile(db)
        policies = db.query(AnswerPolicy).all()

        profile_data = {
            "contact": vp.contact,
            "full_name": vp.full_name,
            "facts": [{"category": f.category, "data": f.data} for f in vp.facts],
        }

        vault_data = {
            p.key: {
                "value": p.value,
                "scope": p.scope,
                "confidence": p.confidence,
                "sensitive": p.sensitive,
                "note": p.note,
            }
            for p in policies
        }

        ext_data_dir = Path(__file__).resolve().parent.parent.parent / "extension" / "data"
        ext_data_dir.mkdir(parents=True, exist_ok=True)

        (ext_data_dir / "default_profile.json").write_text(
            json.dumps(profile_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (ext_data_dir / "default_vault.json").write_text(
            json.dumps(vault_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"✓ Synchronized {len(profile_data['facts'])} facts and {len(vault_data)} policies to extension/data/")
    finally:
        db.close()


def _cmd_extension_sync(args: argparse.Namespace) -> int:
    _sync_extension_data()
    return 0


def _cmd_extension_build(args: argparse.Namespace) -> int:
    _sync_extension_data()
    repo_root = Path(__file__).resolve().parent.parent.parent
    ext_dir = repo_root / "extension"
    releases_dir = repo_root / "releases"
    releases_dir.mkdir(exist_ok=True)

    zip_path = releases_dir / "HunterXJob-Extension-v1.0.0.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in ext_dir.rglob("*"):
            if file.is_file() and not file.name.startswith("."):
                arcname = file.relative_to(ext_dir)
                zipf.write(file, arcname)

    print(f"✓ Packaged portable extension: {zip_path} ({zip_path.stat().st_size} bytes)")
    return 0


def _cmd_extension_info(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    ext_dir = repo_root / "extension"
    print("=================================================================")
    print("  HUNTERXJOB PORTABLE AUTOFILL EXTENSION & IN-PAGE COMPANION")
    print("=================================================================")
    print(f"Extension Directory: {ext_dir}")
    print("\nHow to install in Google Chrome / Brave / Microsoft Edge / Arc:")
    print("  1. Open your browser and navigate to chrome://extensions (or edge://extensions)")
    print("  2. Enable 'Developer mode' toggle in the top right")
    print("  3. Click 'Load unpacked' and select the directory:")
    print(f"     {ext_dir}")
    print("  4. The HunterXJob icon ⚡ will appear in your browser toolbar!")
    print("\nHow to autofill on job boards (Indeed, LinkedIn, GC Jobs, Workday):")
    print("  - When on any job application page or modal, press:")
    print("      Alt + Shift + F  (or Option + Shift + F on Mac)")
    print("    to instantly fill all detected fields with your verified facts.")
    print("  - Review the filled fields highlighted on screen, or click 'Review' in")
    print("    the floating in-page HUD before clicking Submit.")
    print("=================================================================")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("extension", help="HunterXJob portable browser extension manager")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_sync = sub.add_parser("sync", help="sync latest SQLite facts and vault policies into the extension")
    p_sync.set_defaults(handler=_cmd_extension_sync)

    p_build = sub.add_parser("build", help="bundle and package extension into releases/HunterXJob-Extension-v1.0.0.zip")
    p_build.set_defaults(handler=_cmd_extension_build)

    p_info = sub.add_parser("info", help="display installation and keyboard shortcut usage instructions")
    p_info.set_defaults(handler=_cmd_extension_info)
