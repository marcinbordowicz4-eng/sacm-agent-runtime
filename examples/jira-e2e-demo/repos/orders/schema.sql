CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    payment_status TEXT NOT NULL,
    checkout_contract_version INTEGER NOT NULL
);
