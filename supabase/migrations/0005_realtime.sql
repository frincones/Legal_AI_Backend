-- 0005 · Realtime publication para streaming/reconexión
-- Permite al frontend suscribirse a message_parts (replay/multi-tab).

do $$
begin
  begin alter publication supabase_realtime add table messages; exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table message_parts; exception when duplicate_object then null; end;
end $$;
