from app.routers import account, addresses, catalog, checkout, shipping

ALL_ROUTERS = [
    catalog.router,
    account.router,
    addresses.router,
    shipping.router,
    checkout.router,
]
