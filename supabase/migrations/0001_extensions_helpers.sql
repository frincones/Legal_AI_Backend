-- 0001 · Extensions + schema app
-- (los helpers de RLS se crean en 0003, después de que existan las tablas)

create extension if not exists "pgcrypto";
create extension if not exists vector;

create schema if not exists app;
