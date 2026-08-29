# Inventory & Warehouse Cloud Service

A distributed 3-tier cloud service topology:
`gateway-service (port 8000) -> inventory-service (port 8001) -> warehouse-proxy (port 8475) -> warehouse-service (port 8002)`

Protected by [ChangeProof](https://github.com/goodmorningsaksham/ChangeProof) Reliability Gate in CI.
