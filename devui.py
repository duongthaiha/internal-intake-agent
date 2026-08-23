"""Compatibility entry point for the hosted agent DevUI."""

from agents.hosted.devui import main, run_server


__all__ = ["main", "run_server"]


if __name__ == "__main__":
    main()
