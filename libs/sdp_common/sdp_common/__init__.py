"""Shared contracts for the Solution Discovery Platform.

Every service depends on this package and on nothing else of ours. It holds:
- contracts: the JSON packet shapes (pydantic models; JSON Schemas are generated from them)
- events: the CloudEvents envelope and the bus publisher
- clients: thin HTTP clients for the ledger, model gateway and bus
- auth: service tokens and actor propagation
- text: citation verification and chunking
"""
__version__ = "0.1.0"
