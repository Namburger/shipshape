#!/usr/bin/env python3
"""
Ship Shape Marathon Training & Strava Dashboard Server entrypoint.
"""

from strap.handler import DashboardHandler, ReusableTCPServer, run_server

__all__ = ["DashboardHandler", "ReusableTCPServer", "main"]


def main() -> None:
    """Start the local Ship Shape Strava Dashboard server."""
    run_server()


if __name__ == "__main__":
    main()
