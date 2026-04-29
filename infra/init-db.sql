-- Run by postgres docker-entrypoint-initdb.d on first boot.
-- Creates the AgentOS database alongside the default "kachu" database.
CREATE DATABASE agent_platform;
GRANT ALL PRIVILEGES ON DATABASE agent_platform TO kachu;
