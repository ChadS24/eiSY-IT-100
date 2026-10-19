#!/usr/bin/env python3
"""PG3 plugin for a DSC alarm panel through an IT-100, reached over TCP (ser2net)."""
import sys

import udi_interface

from nodes import Controller, PartitionNode, ZoneNode

LOGGER = udi_interface.LOGGER
VERSION = "1.0.0"

if __name__ == "__main__":
    try:
        polyglot = udi_interface.Interface([Controller, PartitionNode, ZoneNode])
        polyglot.start(VERSION)
        Controller(polyglot, "controller", "controller", "DSC Panel")
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    except Exception:
        LOGGER.exception("Unexpected error")
        sys.exit(1)
