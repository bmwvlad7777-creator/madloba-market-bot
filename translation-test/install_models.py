import os
import sys
from pathlib import Path

# Use the same in-project model directory as app.py. Render build-time and
# runtime processes must read the exact same deployed directory.
os.environ["ARGOS_PACKAGES_DIR"] = str(
    Path(__file__).resolve().parent / "argos-packages"
)

import argostranslate.package
import argostranslate.translate

REQUIRED = [
    ("ru", "en"),
    ("en", "ru"),
    ("uk", "en"),
    ("en", "uk"),
]


def main():
    print("Updating Argos package index...", flush=True)
    argostranslate.package.update_package_index()

    packages = argostranslate.package.get_available_packages()

    for src, dst in REQUIRED:
        pkg = next(
            (
                p for p in packages
                if p.from_code == src and p.to_code == dst
            ),
            None,
        )

        if pkg is None:
            print(f"Model not found: {src}->{dst}", flush=True)
            return 2

        print(f"Installing {src}->{dst}...", flush=True)
        path = pkg.download()
        argostranslate.package.install_from_path(path)

    installed_languages = argostranslate.translate.get_installed_languages()
    print("Argos packages directory:", os.environ["ARGOS_PACKAGES_DIR"], flush=True)
    print("Installed languages:")
    for lang in installed_languages:
        print(lang.code, flush=True)

    language_map = {lang.code: lang for lang in installed_languages}
    for src, dst in REQUIRED:
        if src not in language_map or dst not in language_map:
            print(f"Verification failed: missing language {src}->{dst}", flush=True)
            return 3
        if language_map[src].get_translation(language_map[dst]) is None:
            print(f"Verification failed: missing translation {src}->{dst}", flush=True)
            return 4
        print(f"Verified {src}->{dst}", flush=True)

    print("Installation completed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
