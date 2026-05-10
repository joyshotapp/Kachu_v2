-- 由 postgres docker-entrypoint-initdb.d 在首次啟動時執行。
-- 建立 AgentOS 使用的 agent_platform 資料庫（預設 DB 為 kachu_plus，已由 POSTGRES_DB 建立）。
CREATE DATABASE agent_platform;
GRANT ALL PRIVILEGES ON DATABASE agent_platform TO kachu_plus;
