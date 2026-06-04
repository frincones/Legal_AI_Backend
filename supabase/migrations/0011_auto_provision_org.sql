-- 0011 · Auto-provisión de tenant en signup (F6.0 hardening multitenant) — ADITIVO.
-- Al registrarse un usuario nuevo, se le crea su ORG PERSONAL + membership 'admin' + perfil base.
-- Garantiza que todo usuario tenga un workspace aislado por defecto (abogado solo = org de 1;
-- una firma invita miembros que comparten). No toca usuarios existentes.

create or replace function app.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org uuid;
  display text;
begin
  display := coalesce(nullif(new.raw_user_meta_data->>'full_name', ''),
                      split_part(coalesce(new.email, 'usuario'), '@', 1));

  -- Org personal (slug único por user id).
  insert into orgs (slug, name, kind, plan)
  values ('u-' || new.id::text, display || ' (personal)', 'firm', 'trial')
  on conflict (slug) do nothing
  returning id into new_org;

  -- Si ya existía (re-ejecución), recupéralo.
  if new_org is null then
    select id into new_org from orgs where slug = 'u-' || new.id::text;
  end if;

  -- Membership admin (idempotente).
  insert into memberships (org_id, user_id, role, status)
  values (new_org, new.id, 'admin', 'active')
  on conflict (org_id, user_id) do nothing;

  -- Perfil base (idempotente).
  insert into profiles (id, email, full_name)
  values (new.id, coalesce(new.email, ''), display)
  on conflict (id) do nothing;

  return new;
exception when others then
  -- Nunca bloquear el signup por un fallo de provisión: se registra y se puede reparar luego.
  raise warning 'handle_new_user fallo para %: %', new.id, sqlerrm;
  return new;
end
$$;

revoke all on function app.handle_new_user() from public;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function app.handle_new_user();
