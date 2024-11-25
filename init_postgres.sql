-- Создание таблиц пользователей и бюджетов
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_items (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    description TEXT,
    amount DECIMAL(10, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_user_id ON budget_items(user_id);
