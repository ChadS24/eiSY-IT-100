"""
Stand-in for the udi_interface package, just enough to exercise the plugin's nodes
off the eISY. Records drivers, reported commands and added nodes.
"""
import logging

LOGGER = logging.getLogger("udi_interface")


class Custom(dict):
    def __init__(self, poly, name):
        super().__init__()
        self.poly = poly

    def load(self, params):
        self.clear()
        self.update(params or {})

    def __getitem__(self, key):
        return self.get(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)


class Node:
    def __init__(self, poly, primary, address, name):
        self.poly, self.primary, self.address, self.name = poly, primary, address, name
        self._drivers = {d["driver"]: d["value"] for d in self.drivers}
        self.reported = []

    def setDriver(self, driver, value, report=True, force=False, uom=None, text=None):
        self._drivers[driver] = value

    def getDriver(self, driver):
        return self._drivers.get(driver)

    def reportDrivers(self):
        pass

    def reportCmd(self, command, value=None, uom=None):
        self.reported.append(command)


class Interface:
    START = "start"
    CUSTOMPARAMS = "custom_params"
    POLL = "poll"
    STOP = "stop"

    def __init__(self, classes=None):
        self.nodes_internal = {}
        self.handlers = {}

    def subscribe(self, topic, callback, address=None):
        self.handlers.setdefault(topic, []).append(callback)

    def ready(self):
        pass

    def addNode(self, node, conn_status=None, rename=False):
        self.nodes_internal[node.address] = node
        return node

    def getNode(self, address):
        return self.nodes_internal.get(address)

    def getNodes(self):
        return self.nodes_internal

    def updateProfile(self):
        pass

    def setCustomParamsDoc(self):
        pass
