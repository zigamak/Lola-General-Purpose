-- ============================================================
-- Lola Demo Bot — Makinde Kitchen
-- Full Schema
-- Run via: python run_migration.py
-- Or paste into Supabase SQL Editor
-- ============================================================

-- Drop existing tables cleanly
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

-- ============================================================
-- CUSTOMERS
-- One row per WhatsApp number
-- Created automatically when a customer first messages
-- ============================================================
CREATE TABLE customers (
    id              SERIAL PRIMARY KEY,
    phone_number    VARCHAR(20) UNIQUE NOT NULL,
    name            VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- PRODUCTS
-- Menu items — seeded below
-- Manageable via the portal (add, edit, disable, delete)
-- ============================================================
CREATE TABLE products (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    description     TEXT,
    price           INTEGER NOT NULL,       -- in naira
    category        VARCHAR(50) NOT NULL,
    is_available    BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- ORDERS
-- One row per checkout session
-- status:         pending | payment_sent | paid | preparing | on_the_way | delivered | cancelled
-- payment_status: unpaid | paid | failed
-- ============================================================
CREATE TABLE orders (
    id                  SERIAL PRIMARY KEY,
    order_ref           VARCHAR(20) UNIQUE NOT NULL,
    customer_id         INTEGER REFERENCES customers(id),
    delivery_address    TEXT,
    subtotal            INTEGER NOT NULL DEFAULT 0,     -- naira
    delivery_fee        INTEGER NOT NULL DEFAULT 0,     -- naira (0 or 500)
    total               INTEGER NOT NULL DEFAULT 0,     -- naira
    status              VARCHAR(30) NOT NULL DEFAULT 'pending',
    payment_ref         VARCHAR(100),                   -- Paystack reference
    payment_status      VARCHAR(20) NOT NULL DEFAULT 'unpaid',
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- ORDER ITEMS
-- Line items per order
-- name and price are snapshots at time of order
-- (so price changes don't affect historical orders)
-- ============================================================
CREATE TABLE order_items (
    id              SERIAL PRIMARY KEY,
    order_id        INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name            VARCHAR(150) NOT NULL,  -- snapshot
    price           INTEGER NOT NULL,       -- snapshot in naira
    quantity        INTEGER NOT NULL,
    subtotal        INTEGER NOT NULL,       -- price * quantity
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- CONVERSATIONS
-- Every single message — both customer and bot
-- Includes: welcome messages, menu browsing, order chat,
--           order summaries, payment messages
-- role: 'user' | 'assistant'
-- order_id is null until an order is created at checkout
-- ============================================================
CREATE TABLE conversations (
    id              SERIAL PRIMARY KEY,
    customer_id     INTEGER REFERENCES customers(id),
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    role            VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    message         TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_customers_phone        ON customers(phone_number);
CREATE INDEX idx_orders_customer        ON orders(customer_id);
CREATE INDEX idx_orders_ref             ON orders(order_ref);
CREATE INDEX idx_orders_status          ON orders(status);
CREATE INDEX idx_orders_payment_status  ON orders(payment_status);
CREATE INDEX idx_order_items_order      ON order_items(order_id);
CREATE INDEX idx_conversations_customer ON conversations(customer_id);
CREATE INDEX idx_conversations_order    ON conversations(order_id);
CREATE INDEX idx_conversations_created  ON conversations(created_at);
CREATE INDEX idx_products_category      ON products(category);
CREATE INDEX idx_products_available     ON products(is_available);

-- ============================================================
-- SEED: Makinde Kitchen Menu
-- ============================================================
INSERT INTO products (name, description, price, category) VALUES

-- Rice & Grains
('Jollof Rice',          'Party-style smoky jollof, cooked to order',                    2500, 'Rice & Grains'),
('Fried Rice',           'Mixed veggies, liver and prawns',                               2800, 'Rice & Grains'),
('Coconut Rice',         'Fragrant coconut base with assorted protein',                   3000, 'Rice & Grains'),
('White Rice + Stew',    'Plain rice with rich tomato beef stew',                         2000, 'Rice & Grains'),

-- Swallows
('Pounded Yam',          'Smooth, stretchy pounded yam',                                  1500, 'Swallows'),
('Eba (Garri)',           'Regular or yellow, soft or firm on request',                    800,  'Swallows'),
('Amala',                'Dark yam flour swallow, smooth texture',                        1200, 'Swallows'),
('Wheat (Semovita)',      'Light wheat swallow',                                           1000, 'Swallows'),

-- Soups
('Egusi Soup',           'Ground melon with assorted meat and stockfish',                 2500, 'Soups'),
('Efo Riro',             'Rich Yoruba vegetable soup with assorted',                      2500, 'Soups'),
('Banga Soup',           'Delta-style palm nut soup with catfish',                        3000, 'Soups'),
('Oha Soup',             'Igbo-style oha leaf soup with cocoyam',                         2800, 'Soups'),
('Okro Soup',            'Fresh cut okro with assorted meat',                             2200, 'Soups'),

-- Small Chops & Snacks
('Small Chops Platter',  'Puff puff, spring rolls, samosa, peppered gizzard - serves 4',  5500, 'Small Chops & Snacks'),
('Puff Puff (10 pcs)',   'Freshly fried, lightly sweetened',                              1000, 'Small Chops & Snacks'),
('Spring Rolls (5 pcs)', 'Crispy vegetable spring rolls',                                 1500, 'Small Chops & Snacks'),
('Peppered Gizzard',     '100g grilled and peppered chicken gizzard',                     2000, 'Small Chops & Snacks'),
('Moin Moin',            'Steamed bean pudding (one wrap)',                                700,  'Small Chops & Snacks'),

-- Proteins
('Chicken (1 piece)',    'Grilled or fried',                                              1500, 'Proteins'),
('Beef (large cut)',     'Peppered or plain',                                             1500, 'Proteins'),
('Fish (1 piece)',       'Catfish or titus',                                              2000, 'Proteins'),
('Goat Meat (3 pcs)',    'Peppered assorted goat meat',                                   2500, 'Proteins'),
('Shrimp (50g)',         'Seasoned and sauteed',                                          2000, 'Proteins'),

-- Drinks
('Zobo (500ml)',         'Hibiscus drink, lightly spiced and chilled',                     800,  'Drinks'),
('Kunu (500ml)',         'Millet drink, slightly sweet',                                   700,  'Drinks'),
('Chapman (can)',        'Classic Lagos Chapman, mixed on request',                        1200, 'Drinks'),
('Bottled Water (75cl)', 'Chilled',                                                        300,  'Drinks');