-- ============================================================
-- Lola Multi-Vendor Bot — Full Schema + Seed Data
-- Run via: python run_migration.py
-- Or paste into Supabase SQL Editor
-- ============================================================

-- Drop existing tables cleanly (order matters for FK constraints)
DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS deliveries CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS riders CASCADE;
DROP TABLE IF EXISTS vendors CASCADE;

-- ============================================================
-- VENDORS
-- One row per restaurant/vendor
-- Products, orders, and deliveries all reference vendors
-- ============================================================
CREATE TABLE vendors (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(100) NOT NULL,
    description       TEXT,
    type              VARCHAR(50),                      -- 'restaurant', 'pharmacy', 'grocery' etc.
    logo_url          TEXT,
    menu_image_url    TEXT,                             -- shown to customer when they select vendor
    telegram_chat_id  VARCHAR(50),                      -- vendor's personal Telegram for notifications
    whatsapp_number   VARCHAR(20),                      -- vendor's WhatsApp for notifications
    rider_group_chat_id VARCHAR(50),                    -- Telegram group chat id for riders
    zone              VARCHAR(100),
    delivery_fee      INT NOT NULL DEFAULT 500,         -- flat delivery fee in naira
    free_delivery_min INT NOT NULL DEFAULT 5000,        -- free delivery threshold in naira
    opening_hours     TEXT DEFAULT 'Mon–Sat: 10am–9pm, Sun: 12pm–7pm',
    delivery_areas    TEXT DEFAULT 'Lagos and surrounding areas',
    support_contact   VARCHAR(50),
    order_ref_prefix  VARCHAR(10) DEFAULT 'ORD',        -- e.g. 'MK', 'CC', 'MT'
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- RIDERS
-- Delivery riders — created automatically when they first
-- tap Accept in the rider group. No manual setup needed.
-- ============================================================
CREATE TABLE riders (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR(100),
    phone_number     VARCHAR(20),
    telegram_id      VARCHAR(50) UNIQUE,               -- personal Telegram chat_id
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- CUSTOMERS
-- One row per phone number / Telegram chat_id
-- Created automatically on first message
-- ============================================================
CREATE TABLE customers (
    id           SERIAL PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    name         VARCHAR(100),
    platform     VARCHAR(20) DEFAULT 'whatsapp',        -- 'whatsapp' | 'telegram'
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- PRODUCTS
-- Menu items per vendor
-- Manageable via portal (add, edit, disable, delete)
-- ============================================================
CREATE TABLE products (
    id           SERIAL PRIMARY KEY,
    vendor_id    INT NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    name         VARCHAR(150) NOT NULL,
    description  TEXT,
    price        INT NOT NULL,                          -- naira
    category     VARCHAR(100) NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- ORDERS
-- One row per checkout session
-- status:         pending | payment_sent | paid | preparing | on_the_way | delivered | cancelled
-- payment_status: unpaid | paid | failed
-- ============================================================
CREATE TABLE orders (
    id               SERIAL PRIMARY KEY,
    order_ref        VARCHAR(20) UNIQUE NOT NULL,
    customer_id      INT NOT NULL REFERENCES customers(id),
    vendor_id        INT REFERENCES vendors(id),
    delivery_address TEXT,
    subtotal         INT NOT NULL DEFAULT 0,            -- naira
    delivery_fee     INT NOT NULL DEFAULT 0,            -- naira
    total            INT NOT NULL DEFAULT 0,            -- naira
    status           VARCHAR(30) NOT NULL DEFAULT 'pending',
    payment_ref      VARCHAR(100),
    payment_status   VARCHAR(20) NOT NULL DEFAULT 'unpaid',
    platform         VARCHAR(20) DEFAULT 'whatsapp',    -- 'whatsapp' | 'telegram'
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- ORDER ITEMS
-- Line items per order — price/name are snapshots at order time
-- ============================================================
CREATE TABLE order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INT REFERENCES products(id) ON DELETE SET NULL,
    name       VARCHAR(150) NOT NULL,
    price      INT NOT NULL,                            -- snapshot naira
    quantity   INT NOT NULL DEFAULT 1,
    subtotal   INT NOT NULL,                            -- price * quantity
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- PAYMENTS
-- Logs every Paystack payment event
-- ============================================================
CREATE TABLE payments (
    id              SERIAL PRIMARY KEY,
    order_id        INT REFERENCES orders(id),
    order_ref       VARCHAR(50),
    amount          INT,                                -- kobo
    payment_ref     VARCHAR(100),
    gateway         VARCHAR(30) DEFAULT 'paystack',
    status          VARCHAR(30) DEFAULT 'pending',      -- pending | success | failed
    webhook_payload JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- DELIVERIES
-- One row per order — tracks rider assignment and status
-- status: pending | accepted | picked_up | delivered
-- PIN is generated on payment and sent privately to assigned rider
-- ============================================================
CREATE TABLE deliveries (
    id                SERIAL PRIMARY KEY,
    order_id          INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    rider_id          INT REFERENCES riders(id),
    rider_telegram_id VARCHAR(50),                      -- personal Telegram id of assigned rider
    rider_name        VARCHAR(100),
    rider_phone       VARCHAR(20),
    group_message_id  VARCHAR(50),                      -- Telegram message_id in rider group (for editing)
    status            VARCHAR(30) NOT NULL DEFAULT 'pending',
    pin               VARCHAR(4),                       -- shown to vendor to verify rider identity
    accepted_at       TIMESTAMP,
    picked_up_at      TIMESTAMP,
    delivered_at      TIMESTAMP,
    timeout_at        TIMESTAMP,                        -- if no accept by this time, re-post to group
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- NOTIFICATIONS
-- Log of every outbound message sent by the bot
-- recipient_type: 'customer' | 'vendor' | 'rider'
-- ============================================================
CREATE TABLE notifications (
    id             SERIAL PRIMARY KEY,
    order_id       INT REFERENCES orders(id),
    recipient_type VARCHAR(20),
    platform       VARCHAR(20),                         -- 'whatsapp' | 'telegram'
    chat_id        VARCHAR(50),
    message        TEXT,
    status         VARCHAR(20) DEFAULT 'sent',          -- sent | failed
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- CONVERSATIONS
-- Every message — both customer and bot
-- role: 'user' | 'assistant'
-- ============================================================
CREATE TABLE conversations (
    id          SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id),
    order_id    INT REFERENCES orders(id) ON DELETE SET NULL,
    role        VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    message     TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_vendors_active           ON vendors(is_active);
CREATE INDEX idx_riders_telegram          ON riders(telegram_id);
CREATE INDEX idx_customers_phone          ON customers(phone_number);
CREATE INDEX idx_products_vendor          ON products(vendor_id);
CREATE INDEX idx_products_category        ON products(category);
CREATE INDEX idx_products_available       ON products(is_available);
CREATE INDEX idx_orders_customer          ON orders(customer_id);
CREATE INDEX idx_orders_vendor            ON orders(vendor_id);
CREATE INDEX idx_orders_ref               ON orders(order_ref);
CREATE INDEX idx_orders_status            ON orders(status);
CREATE INDEX idx_orders_payment_status    ON orders(payment_status);
CREATE INDEX idx_order_items_order        ON order_items(order_id);
CREATE INDEX idx_payments_order           ON payments(order_id);
CREATE INDEX idx_deliveries_order         ON deliveries(order_id);
CREATE INDEX idx_deliveries_rider         ON deliveries(rider_id);
CREATE INDEX idx_notifications_order      ON notifications(order_id);
CREATE INDEX idx_conversations_customer   ON conversations(customer_id);
CREATE INDEX idx_conversations_order      ON conversations(order_id);
CREATE INDEX idx_conversations_created    ON conversations(created_at);

-- ============================================================
-- SEED: Vendors
-- ============================================================
INSERT INTO vendors (name, description, type, menu_image_url, rider_group_chat_id,
                     zone, delivery_fee, free_delivery_min, opening_hours,
                     delivery_areas, support_contact, order_ref_prefix, is_active)
VALUES
(
    'Makinde Kitchen',
    'Lagos-based Nigerian comfort food — soups, rice, swallows, small chops and more.',
    'restaurant',
    'https://eventio.africa/wp-content/uploads/2026/04/lola-general-purpose.jpg',
    '-5232735234',
    'Lagos Mainland',
    500, 5000,
    'Mon–Sat: 10am–9pm, Sun: 12pm–7pm',
    'Surulere, Lagos Island, VI, Yaba, Lekki and surrounding areas',
    '+2348000000001',
    'MK',
    TRUE
),
(
    'Campus Canteen',
    'Quick affordable meals for students — rice, stew, snacks and drinks.',
    'restaurant',
    'https://eventio.africa/wp-content/uploads/2026/04/menu2-e1776438245994.jpg',
    '-5232735234',
    'University Campus',
    200, 3000,
    'Mon–Fri: 7am–8pm, Sat: 8am–5pm',
    'On-campus and surrounding hostels',
    '+2348000000002',
    'CC',
    TRUE
),
(
    'Mama T Buka',
    'Authentic home-style Nigerian buka — fresh soups, swallows, and pepper soup daily.',
    'restaurant',
    'https://eventio.africa/wp-content/uploads/2026/04/menu1.jpg',
    '-5232735234',
    'Lagos Island',
    400, 4000,
    'Mon–Sun: 8am–7pm',
    'Lagos Island, Apongbon, CMS and environs',
    '+2348000000003',
    'MT',
    TRUE
);

-- ============================================================
-- SEED: Makinde Kitchen Products (vendor_id = 1)
-- ============================================================
INSERT INTO products (vendor_id, name, description, category, price, is_available) VALUES
-- Rice & Grains
(1, 'Jollof Rice',          'Party-style smoky jollof, cooked to order',                  'Rice & Grains', 2500, TRUE),
(1, 'Fried Rice',           'Mixed veggies, liver and prawns',                             'Rice & Grains', 2800, TRUE),
(1, 'Coconut Rice',         'Fragrant coconut base with assorted protein',                 'Rice & Grains', 3000, TRUE),
(1, 'White Rice + Stew',    'Plain rice with rich tomato beef stew',                       'Rice & Grains', 2000, TRUE),
-- Swallows
(1, 'Pounded Yam',          'Smooth, stretchy pounded yam',                                'Swallows',      1500, TRUE),
(1, 'Eba (Garri)',          'Regular or yellow, soft or firm on request',                  'Swallows',       800, TRUE),
(1, 'Amala',                'Dark yam flour swallow, smooth texture',                      'Swallows',      1200, TRUE),
(1, 'Wheat (Semovita)',     'Light wheat swallow',                                         'Swallows',      1000, TRUE),
-- Soups
(1, 'Egusi Soup',           'Ground melon with assorted meat and stockfish',               'Soups',         2500, TRUE),
(1, 'Efo Riro',             'Rich Yoruba vegetable soup with assorted',                    'Soups',         2500, TRUE),
(1, 'Banga Soup',           'Delta-style palm nut soup with catfish',                      'Soups',         3000, TRUE),
(1, 'Oha Soup',             'Igbo-style oha leaf soup with cocoyam',                       'Soups',         2800, TRUE),
(1, 'Okro Soup',            'Fresh cut okro with assorted meat',                           'Soups',         2200, TRUE),
-- Small Chops & Snacks
(1, 'Small Chops Platter',  'Puff puff, spring rolls, samosa, peppered gizzard - serves 4', 'Small Chops & Snacks', 5500, TRUE),
(1, 'Puff Puff (10 pcs)',   'Freshly fried, lightly sweetened',                            'Small Chops & Snacks', 1000, TRUE),
(1, 'Spring Rolls (5 pcs)', 'Crispy vegetable spring rolls',                               'Small Chops & Snacks', 1500, TRUE),
(1, 'Peppered Gizzard',     '100g grilled and peppered chicken gizzard',                   'Small Chops & Snacks', 2000, TRUE),
(1, 'Moin Moin',            'Steamed bean pudding (one wrap)',                             'Small Chops & Snacks',  700, TRUE),
-- Proteins
(1, 'Chicken (1 piece)',    'Grilled or fried',                                            'Proteins',      1500, TRUE),
(1, 'Beef (large cut)',     'Peppered or plain',                                           'Proteins',      1500, TRUE),
(1, 'Fish (1 piece)',       'Catfish or titus',                                            'Proteins',      2000, TRUE),
(1, 'Goat Meat (3 pcs)',    'Peppered assorted goat meat',                                 'Proteins',      2500, TRUE),
(1, 'Shrimp (50g)',         'Seasoned and sauteed',                                        'Proteins',      2000, TRUE),
-- Drinks
(1, 'Zobo (500ml)',         'Hibiscus drink, lightly spiced and chilled',                  'Drinks',         800, TRUE),
(1, 'Kunu (500ml)',         'Millet drink, slightly sweet',                                'Drinks',         700, TRUE),
(1, 'Chapman (can)',        'Classic Lagos Chapman',                                       'Drinks',        1200, TRUE),
(1, 'Bottled Water (75cl)', 'Chilled',                                                     'Drinks',         300, TRUE);

-- ============================================================
-- SEED: Campus Canteen Products (vendor_id = 2)
-- ============================================================
INSERT INTO products (vendor_id, name, description, category, price, is_available) VALUES
(2, 'Rice + Stew',          'White rice with tomato stew and one protein',                 'Meals',          800, TRUE),
(2, 'Jollof Rice',          'Party-style jollof with chicken',                             'Meals',         1000, TRUE),
(2, 'Fried Rice',           'Fried rice with mixed veggies',                               'Meals',         1000, TRUE),
(2, 'Beans + Plantain',     'Cooked beans with fried ripe plantain',                       'Meals',          700, TRUE),
(2, 'Yam + Egg Sauce',      'Boiled yam with scrambled egg sauce',                         'Meals',          800, TRUE),
(2, 'Indomie (Regular)',    'Spicy noodles with egg',                                      'Snacks',         400, TRUE),
(2, 'Indomie (Large)',      'Large portion with egg and sausage',                          'Snacks',         700, TRUE),
(2, 'Puff Puff (5 pcs)',    'Freshly fried',                                               'Snacks',         300, TRUE),
(2, 'Egg Roll',             'Pastry with boiled egg filling',                              'Snacks',         300, TRUE),
(2, 'Chicken Piece',        'Fried chicken — one piece',                                   'Proteins',       600, TRUE),
(2, 'Beef Piece',           'Peppered beef',                                               'Proteins',       500, TRUE),
(2, 'Bottled Water (50cl)', NULL,                                                          'Drinks',         200, TRUE),
(2, 'Soft Drink (35cl)',    'Coke, Fanta, or Sprite',                                      'Drinks',         400, TRUE),
(2, 'Zobo (350ml)',         'Chilled hibiscus drink',                                      'Drinks',         300, TRUE);

-- ============================================================
-- SEED: Mama T Buka Products (vendor_id = 3)
-- ============================================================
INSERT INTO products (vendor_id, name, description, category, price, is_available) VALUES
-- Swallows
(3, 'Pounded Yam',          'Smooth and stretchy',                                         'Swallows',      1200, TRUE),
(3, 'Eba',                  'Yellow or white garri',                                       'Swallows',       600, TRUE),
(3, 'Amala',                'Classic dark amala',                                          'Swallows',       800, TRUE),
(3, 'Fufu',                 'Fermented cassava fufu',                                      'Swallows',       700, TRUE),
-- Soups
(3, 'Egusi Soup',           'Mama T''s secret recipe with goat meat',                      'Soups',         1800, TRUE),
(3, 'Gbegiri + Ewedu',      'Yoruba classic — bean soup with jute leaves',                 'Soups',         1500, TRUE),
(3, 'Buka Stew',            'Rich tomato stew with assorted meat',                         'Soups',         1500, TRUE),
(3, 'Okro Soup',            'Fresh okro with stockfish and assorted',                      'Soups',         1800, TRUE),
(3, 'Ofe Onugbu',           'Igbo bitter leaf soup with oxtail',                           'Soups',         2000, TRUE),
-- Pepper Soup
(3, 'Catfish Pepper Soup',  'Fresh point-and-kill catfish',                                'Pepper Soup',   2500, TRUE),
(3, 'Goat Meat Pepper Soup','Spicy goat meat in pepper soup broth',                        'Pepper Soup',   2000, TRUE),
(3, 'Cow Leg Pepper Soup',  'Tender cow leg in spiced broth',                              'Pepper Soup',   2200, TRUE),
-- Proteins
(3, 'Goat Meat (2 pcs)',    'Peppered or plain',                                           'Proteins',      1500, TRUE),
(3, 'Assorted Meat',        'Mixed selection of cow parts',                                'Proteins',      1500, TRUE),
(3, 'Stockfish',            'Dried and rehydrated stockfish',                              'Proteins',       800, TRUE),
(3, 'Ponmo (cow skin)',     'Soft-cooked cow skin',                                        'Proteins',       600, TRUE),
-- Drinks
(3, 'Zobo (500ml)',         'House-made hibiscus drink',                                   'Drinks',         500, TRUE),
(3, 'Kunu (500ml)',         'Chilled millet drink',                                        'Drinks',         500, TRUE),
(3, 'Palm Wine (500ml)',    'Fresh palm wine',                                             'Drinks',         800, TRUE),
(3, 'Bottled Water (75cl)', NULL,                                                          'Drinks',         200, TRUE);