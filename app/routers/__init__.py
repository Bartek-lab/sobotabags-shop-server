from app.routers import account, addresses, catalog, checkout, orders, shipping

ALL_ROUTERS = [
    catalog.router,
    account.router,
    addresses.router,
    orders.router,
    shipping.router,
    checkout.router,
]
