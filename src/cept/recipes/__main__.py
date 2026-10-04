"""Installed recipe entry point: python -m cept.recipes <recipe>."""
from cept.recipes.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
