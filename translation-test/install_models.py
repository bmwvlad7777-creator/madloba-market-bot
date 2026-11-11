import sys
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

    print("Installed languages:")
    for lang in argostranslate.translate.get_installed_languages():
        print(lang.code)

    print("Installation completed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
